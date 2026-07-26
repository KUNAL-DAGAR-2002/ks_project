import base64, json, re
from pathlib import Path
import httpx
from .config import settings

class GeminiProvider:
    endpoint = "https://generativelanguage.googleapis.com/v1beta/models"

    @staticmethod
    def _raise_api_error(exc: httpx.HTTPStatusError) -> None:
        status = exc.response.status_code
        try: message = exc.response.json().get("error", {}).get("message", "")
        except Exception: message = ""
        if status in {401, 403}: raise RuntimeError("Gemini rejected the API key. Configure a valid Gemini API key.") from exc
        if status == 429: raise RuntimeError("Gemini free-tier limit reached. Please wait and try again.") from exc
        raise RuntimeError(f"Gemini request failed ({status}){': ' + message if message else ''}") from exc

    async def _generate(self, prompt: str, *, image: tuple[str, str] | None = None, search: bool = False) -> tuple[dict, dict]:
        if not settings.gemini_api_key: raise RuntimeError("KIRANA_GEMINI_API_KEY is not configured")
        parts = [{"text": prompt}]
        if image:
            mime, data = image; parts.append({"inline_data": {"mime_type": mime, "data": data}})
        payload: dict = {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"temperature": 0}}
        if search: payload["tools"] = [{"google_search": {}}]
        else: payload["generationConfig"]["responseMimeType"] = "application/json"
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(f"{self.endpoint}/{settings.gemini_model}:generateContent", headers={"x-goog-api-key": settings.gemini_api_key}, json=payload)
            try: response.raise_for_status()
            except httpx.HTTPStatusError as exc: self._raise_api_error(exc)
        body = response.json()
        try: text = "".join(part.get("text", "") for part in body["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError) as exc: raise RuntimeError("Gemini returned no usable response.") from exc
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
        try: return json.loads(text), body
        except json.JSONDecodeError as exc: raise RuntimeError("Gemini returned invalid structured data.") from exc

    async def extract(self, path: str, mime: str, document_type: str) -> dict:
        data = base64.b64encode(Path(path).read_bytes()).decode()
        rows = ([{"customer_name":"","amount":0,"entry_type":"credit_sale","confidence":0}] if document_type == "udhaar" else [{"product_name":"","quantity":0,"unit":"piece","total_price":0,"mrp":0,"trade_form":"standard","pack_size":0,"pack_unit":"","form_confidence":"confirmed","confidence":0}])
        schema = {"document_type":document_type,"supplier_or_customer":"","invoice_number":"","date":"","payment_mode":"cash","rows":rows,"total":0,"confidence":0}
        prompt = f"""Extract this Indian kirana {document_type} document. Hindi, English and Hinglish are possible. Return JSON only matching {json.dumps(schema)}.
Row price is the total for the complete row, not unit price. Extract payment_mode only as cash, upi, or udhaar. Treat Credit as udhaar. If payment mode is absent or unclear, return cash. Do not return card. For sugar, salt, rice, atta/flour, dal, pulses and similar weight-based staples, classify trade_form as packed or loose when the document provides evidence. Packet/pouch/piece/bottle or an explicit branded fixed pack means packed. A quantity measured directly in kg/g without packaging usually means loose. If packed versus loose is genuinely unclear, set trade_form to loose and form_confidence to assumed so the shopkeeper can correct it. Use form_confidence confirmed only when the document supports the choice. Extract pack_size and pack_unit when present. For other products use trade_form standard. Never invent unreadable values; use empty strings or zero and low confidence."""
        result, _ = await self._generate(prompt, image=(mime, data)); return result

    async def parse_text(self, text: str, document_type: str) -> dict:
        shape = ('{"rows":[{"customer_name":"","amount":0,"entry_type":"credit_sale","confidence":0}],"confidence":0}' if document_type == "udhaar" else '{"rows":[{"product_name":"","quantity":0,"total_price":0,"unit":"packet","mrp":0,"confidence":0}],"confidence":0}')
        prompt = f"Map this informal Indian kirana {document_type} entry into rows. Infer practical Indian retail units. The last rupee value is TOTAL PRICE for the row, never per-unit price. For udhaar infer credit_sale or payment_received. Hindi and Hinglish are possible. Return JSON only matching {shape}. Input: {text}"
        result, _ = await self._generate(prompt); return result

    async def match_existing_product(self, entered_name: str, candidates: list[dict]) -> dict:
        """Choose only from the supplied catalogue; never invent a product."""
        prompt = f"""Match the kirana sales entry '{entered_name}' to exactly one existing product below, allowing spelling mistakes, missing letters, Hindi-English transliteration, brand spelling, and pack-size wording. Pack size must remain compatible. Never invent a product. If no candidate is clearly the same item, return match_id as an empty string. Return JSON only as {{"match_id":"","confidence":0,"reason":""}}. Candidates: {json.dumps(candidates, ensure_ascii=False)}"""
        result, _ = await self._generate(prompt)
        allowed = {str(x["id"]) for x in candidates}
        match_id = str(result.get("match_id", ""))
        confidence = float(result.get("confidence", 0) or 0)
        if match_id not in allowed or confidence < 0.72:
            return {"match_id":"","confidence":confidence,"reason":result.get("reason","")}
        return {"match_id":match_id,"confidence":confidence,"reason":result.get("reason","")}

    async def research_indian_kirana_unit(self, product_name: str) -> dict:
        allowed = ["piece","packet","bottle","litre","kilogram","gram","box","carton","dozen"]
        prompt = f"Search once for how '{product_name}' is sold to customers by Indian kirana stores. Return JSON only with selling_unit (one of {allowed}), acceptable_units (using only those values), and reasoning (one sentence). For milk include litre and kilogram. Do not include prices."
        data, response = await self._generate(prompt, search=True)
        unit = str(data.get("selling_unit", "piece")).lower(); data["selling_unit"] = unit if unit in allowed else "piece"
        data["acceptable_units"] = [str(x).lower() for x in data.get("acceptable_units", []) if str(x).lower() in allowed]
        lower = product_name.lower()
        if "milk" in lower: data["selling_unit"] = "litre"; data["acceptable_units"] = list(dict.fromkeys([*data["acceptable_units"],"litre","kilogram"]))
        elif re.search(r"\b\d+(\.\d+)?\s*(ml|l|litre|liter)\b", lower): data["selling_unit"] = "litre"
        elif re.search(r"\b\d+(\.\d+)?\s*(kg|g|gram)\b", lower): data["selling_unit"] = "kilogram"
        chunks = response.get("candidates", [{}])[0].get("groundingMetadata", {}).get("groundingChunks", [])
        data["sources"] = [c["web"]["uri"] for c in chunks if c.get("web", {}).get("uri")][:5]
        return data

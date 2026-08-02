import asyncio, base64, json, re
from datetime import datetime, timezone
from pathlib import Path
import httpx
from .config import settings

class GeminiProvider:
    endpoint = "https://generativelanguage.googleapis.com/v1beta/models"
    _market_cache: dict = {}
    _market_cache_day: str = ""
    _market_task: asyncio.Task | None = None

    def __init__(self, usage_recorder=None):
        self.usage_recorder = usage_recorder

    @staticmethod
    def _raise_api_error(exc: httpx.HTTPStatusError) -> None:
        status = exc.response.status_code
        try: message = exc.response.json().get("error", {}).get("message", "")
        except Exception: message = ""
        if status in {401, 403}: raise RuntimeError("Gemini rejected the API key. Configure a valid Gemini API key.") from exc
        if status == 429: raise RuntimeError("Gemini free-tier limit reached. Please wait and try again.") from exc
        raise RuntimeError(f"Gemini request failed ({status}){': ' + message if message else ''}") from exc

    @staticmethod
    def _parse_json(text: str) -> dict:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
        attempts = [cleaned]
        first_object = cleaned.find("{")
        if first_object >= 0: attempts.append(cleaned[first_object:])
        decoder = json.JSONDecoder()
        for candidate in attempts:
            try:
                value, _ = decoder.raw_decode(candidate.lstrip())
                if isinstance(value, dict): return value
            except json.JSONDecodeError:
                continue
        raise RuntimeError("Gemini returned invalid structured data.")

    async def _generate(self, prompt: str, *, image: tuple[str, str] | None = None, search: bool = False, max_tokens: int = 1600) -> tuple[dict, dict]:
        if not settings.gemini_api_key: raise RuntimeError("KIRANA_GEMINI_API_KEY is not configured")
        parts = [{"text": prompt}]
        if image:
            mime, data = image; parts.append({"inline_data": {"mime_type": mime, "data": data}})
        payload: dict = {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"maxOutputTokens": max_tokens}}
        if search: payload["tools"] = [{"google_search": {}}]
        else: payload["generationConfig"]["responseMimeType"] = "application/json"
        configured_model = settings.gemini_model.strip() or "gemini-3.5-flash"
        models = [configured_model]
        if configured_model != "gemini-flash-latest": models.append("gemini-flash-latest")
        async with httpx.AsyncClient(timeout=45) as client:
            for index, model in enumerate(models):
                generation_config = payload["generationConfig"]
                generation_config["thinkingConfig"] = ({"thinkingBudget": 0} if "gemini-2.5" in model else {"thinkingLevel": "minimal"})
                response = await client.post(f"{self.endpoint}/{model}:generateContent", headers={"x-goog-api-key": settings.gemini_api_key}, json=payload)
                if response.status_code == 404 and index + 1 < len(models):
                    continue
                if response.status_code == 400:
                    # Some API/model combinations reject thinking controls even
                    # though generation itself is supported. Retry once using
                    # only the universally supported generation fields.
                    generation_config.pop("thinkingConfig", None)
                    response = await client.post(f"{self.endpoint}/{model}:generateContent", headers={"x-goog-api-key": settings.gemini_api_key}, json=payload)
                try: response.raise_for_status()
                except httpx.HTTPStatusError as exc: self._raise_api_error(exc)
                break
        body = response.json()
        if self.usage_recorder:
            usage=body.get("usageMetadata",{})
            self.usage_recorder({
                "prompt_tokens":int(usage.get("promptTokenCount",0) or 0),
                "output_tokens":int(usage.get("candidatesTokenCount",0) or 0),
                "total_tokens":int(usage.get("totalTokenCount",0) or 0),
            })
        try: text = "".join(part.get("text", "") for part in body["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError) as exc: raise RuntimeError("Gemini returned no usable response.") from exc
        return self._parse_json(text), body

    async def extract(self, path: str, mime: str, document_type: str) -> dict:
        data = base64.b64encode(Path(path).read_bytes()).decode()
        if document_type == "udhaar":
            rows = [{"date":"","customer_name":"","products":"","amount":0,"total_present":False,"given":0,"confidence":0}]
        elif document_type == "supplier_payment":
            rows = [{"date":"","dealer_name":"","amount_paid":0,"confidence":0}]
        else:
            rows = [{"product_name":"","quantity":0,"unit":"piece","total_price":0,"mrp":0,"trade_form":"standard","pack_size":0,"pack_unit":"","form_confidence":"confirmed","confidence":0}]
        schema = {"document_type":document_type,"supplier_or_customer":"","invoice_number":"","date":"","payment_mode":"cash","rows":rows,"total":0,"confidence":0}
        prompt = f"""Extract this Indian kirana {document_type} document. Hindi, English and Hinglish are possible. Return JSON only matching {json.dumps(schema)}.
For an udhaar image, split customer entries when there are two blank lines or another clear large separator. Return one row per customer block. Each row must contain date in YYYY-MM-DD format, customer_name, products as one comma-separated string that keeps product quantities, amount as the explicitly written cumulative Total Udhaar, total_present true only when that cumulative total is visibly written, and given as the explicitly written cumulative Paid total. Total Udhaar and Paid are lifetime-to-date customer totals, never transaction amounts. If Total Udhaar is absent, return amount 0 and total_present false so the app carries forward the customer's stored total. Product quantities never imply a rupee amount. A payment-only block may omit Date; leave date empty so the app uses the current date. Do not calculate Pending; the app calculates Total Udhaar minus Paid.
For a supplier_payment image, return one row per clearly separated dealer payment block. Extract date in YYYY-MM-DD format, dealer_name exactly as written, and amount_paid as the payment made on that date. This is a payment transaction, not a cumulative lifetime Paid value. Never treat a customer payment as a dealer payment and never invent a missing amount.
Row price is the total for the complete row, not unit price. Extract payment_mode only as cash, upi, or udhaar. Treat Credit as udhaar. If payment mode is absent or unclear, return cash. Do not return card. For sugar, salt, rice, atta/flour, dal, pulses and similar weight-based staples, classify trade_form as packed or loose when the document provides evidence. Packet/pouch/piece/bottle or an explicit branded fixed pack means packed. A quantity measured directly in kg/g without packaging usually means loose. If packed versus loose is genuinely unclear, set trade_form to loose and form_confidence to assumed so the shopkeeper can correct it. Use form_confidence confirmed only when the document supports the choice. Extract pack_size and pack_unit when present. For other products use trade_form standard. Never invent unreadable values; use empty strings or zero and low confidence."""
        result, _ = await self._generate(prompt, image=(mime, data)); return result

    async def parse_text(self, text: str, document_type: str) -> dict:
        shape = ('{"rows":[{"date":"","customer_name":"","products":"","amount":0,"total_present":false,"given":0,"confidence":0}],"confidence":0}' if document_type == "udhaar" else '{"rows":[{"product_name":"","quantity":0,"total_price":0,"unit":"packet","mrp":0,"confidence":0}],"confidence":0}')
        prompt = f"Map this informal Indian kirana {document_type} entry into rows. Infer practical Indian retail units. For udhaar, amount is the explicitly written cumulative lifetime Total Udhaar, given is the cumulative lifetime Paid total, and products must be comma-separated. Set total_present true only when Total Udhaar is visibly written. When Paid is present but no total is written, return amount 0 and total_present false so the stored customer total is carried forward. Hindi and Hinglish are possible. Return JSON only matching {shape}. Input: {text}"
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

    async def classify_kirana_category(self, product_name: str, allowed_categories: list[str]) -> dict:
        """Classify a product into the controlled tenant taxonomy."""
        prompt = f"""Classify the Indian kirana product '{product_name}' into exactly one category from this allowed list:
{json.dumps(allowed_categories, ensure_ascii=False)}
Use the product's actual type, not its brand, pack size, loose/packed form, or unit. For example Aashirvaad Atta is Staples & Grains, Tata Salt is Spices & Condiments, Amul Milk is Dairy, and Parle-G is Biscuits & Snacks.
Return JSON only matching {{"category":"","confidence":0,"reason":""}}. The category must exactly match one allowed value."""
        result, _ = await self._generate(prompt, max_tokens=180)
        category = str(result.get("category", "")).strip()
        if category not in allowed_categories:
            raise RuntimeError("Gemini returned a category outside the approved taxonomy.")
        return {"category": category, "confidence": float(result.get("confidence", 0) or 0), "reason": str(result.get("reason", ""))}

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

    async def _fetch_market_context(self) -> dict:
        shape = {"observations": [], "recommended_tactics": [], "sources": []}
        prompt = f"""Search for current Indian kirana, grocery and FMCG retail trends useful to a small neighbourhood shop. Focus on demand, pricing, assortment, inventory, UPI, promotions and seasonality. Avoid generic global advice. Return concise JSON only matching {json.dumps(shape)}. Put source titles or publisher names in sources."""
        data, response = await self._generate(prompt, search=True, max_tokens=700)
        chunks = response.get("candidates", [{}])[0].get("groundingMetadata", {}).get("groundingChunks", [])
        data["sources"] = [
            {"title": c.get("web", {}).get("title", "Market source"), "url": c.get("web", {}).get("uri", "")}
            for c in chunks if c.get("web", {}).get("uri")
        ][:5]
        type(self)._market_cache = data
        type(self)._market_cache_day = datetime.now(timezone.utc).date().isoformat()
        return data

    def warm_market_context(self) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        cls = type(self)
        if cls._market_cache_day == today and cls._market_cache: return
        if cls._market_task and not cls._market_task.done(): return
        cls._market_task = asyncio.create_task(self._fetch_market_context())
        cls._market_task.add_done_callback(lambda task: None if task.cancelled() else task.exception())

    async def current_market_context(self) -> dict:
        today = datetime.now(timezone.utc).date().isoformat()
        cls = type(self)
        if cls._market_cache_day == today and cls._market_cache: return cls._market_cache
        if not cls._market_task or cls._market_task.done(): cls._market_task = asyncio.create_task(self._fetch_market_context())
        try: return await cls._market_task
        except Exception:
            return {"observations":[],"recommended_tactics":[],"sources":[],"note":"Live market context was temporarily unavailable; the answer is based primarily on shop data."}
        finally: cls._market_task = None

    async def business_advice(self, question: str, business_context: dict, history: list[dict], language: str = "en") -> dict:
        shape = {
            "answer": "",
            "data_observations": [],
            "market_observations": [],
            "recommended_actions": [{"action": "", "why": "", "priority": "high"}],
            "follow_up_questions": [],
        }
        market_context = await self.current_market_context()
        language_name = {"en":"English","hi":"Hindi","mr":"Marathi","gu":"Gujarati","kn":"Kannada","ta":"Tamil"}.get(language, "English")
        prompt = f"""You are a practical Indian kirana business analyst. Answer the shopkeeper's question using:
- 70% weight: the tenant-scoped shop data supplied below.
- 30% weight: today's cached Indian kirana/FMCG/retail market research supplied below.

Never invent shop numbers. Explicitly say when the shop does not have enough recorded data. Distinguish the shop's facts from external market observations. Give 2-5 specific actions with a reason and high/medium/low priority. Keep the answer concise, simple and useful. Do not expose internal IDs or personal customer information. Money is INR.

LANGUAGE REQUIREMENT: Write every user-visible JSON value entirely in {language_name}. Do not mix in English when {language_name} is not English. Keep only unavoidable product, brand, customer and supplier names, plus common acronyms such as UPI/GST, unchanged. JSON property names and priority enum values must remain exactly as shown in the schema.

Return JSON only matching this shape: {json.dumps(shape)}
Shop data: {json.dumps(business_context, ensure_ascii=False, default=str)}
Current market research: {json.dumps(market_context, ensure_ascii=False, default=str)}
Recent conversation: {json.dumps(history[-6:], ensure_ascii=False)}
Question: {question}"""
        data, _ = await self._generate(prompt, max_tokens=1100)
        data["sources"] = market_context.get("sources", [])[:5]
        return data

    async def report_insights(self, report_summary: dict, language: str = "en") -> dict:
        market_context = await self.current_market_context()
        shape = {"headline":"","summary":"","actions":[{"priority":"high","title":"","reason":"","next_step":""}],"risks":[],"opportunities":[]}
        language_name = {"en":"English","hi":"Hindi","mr":"Marathi","gu":"Gujarati","kn":"Kannada","ta":"Tamil"}.get(language, "English")
        prompt = f"""You are an Indian kirana business analyst. Turn the supplied aggregated report into clear, actionable owner advice.
Use 70% weight from the shop summary and 30% from the current market context. Do not ask for or invent raw transactions. Respect the exact report period. Use product velocity, profit, bottom sellers, categories, Pareto concentration and restock signals. Recommend 3-6 concrete actions, ordered high to low priority. Distinguish evidence from suggestion. If data is sparse, say so. Keep language simple and concise.
LANGUAGE REQUIREMENT: Write every user-visible JSON value entirely in {language_name}. Do not mix in English when {language_name} is not English. Keep only unavoidable product, brand, customer and supplier names, plus common acronyms such as UPI/GST, unchanged. JSON property names and priority enum values must remain exactly as shown in the schema.
Return JSON only matching {json.dumps(shape)}.
Aggregated shop report: {json.dumps(report_summary,ensure_ascii=False,default=str)}
Current market context: {json.dumps(market_context,ensure_ascii=False,default=str)}"""
        data,_=await self._generate(prompt,max_tokens=1000)
        data["sources"]=market_context.get("sources",[])[:5]
        return data

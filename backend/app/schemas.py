from pydantic import BaseModel, Field, field_validator

class OTPRequest(BaseModel):
    mobile: str = Field(pattern=r"^[6-9]\d{9}$")

class OTPVerify(OTPRequest):
    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=2, max_length=120)

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class OnboardingRequest(BaseModel):
    business_name: str = Field(min_length=2, max_length=160)
    store_name: str = Field(min_length=2, max_length=160)
    city: str = Field(min_length=2, max_length=80)
    state: str = Field(min_length=2, max_length=80)
    pin_code: str = Field(pattern=r"^[1-9]\d{5}$")
    preferred_language: str = "en"
    working_style: list[str] = []
    enabled_modules: list[str] = []

    @field_validator("preferred_language")
    @classmethod
    def valid_language(cls, value: str):
        if value not in {"en", "hi", "hinglish"}: raise ValueError("Unsupported language")
        return value

class BusinessOut(BaseModel):
    id: str
    name: str
    role: str
    onboarding_complete: bool

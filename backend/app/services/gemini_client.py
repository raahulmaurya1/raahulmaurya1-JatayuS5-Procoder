import google.generativeai as genai
from app.config import Settings
from loguru import logger

settings = Settings()

# Configure Gemini with the API Key
if settings.GEMINI_API_KEY:
    genai.configure(api_key=settings.GEMINI_API_KEY)
else:
    logger.warning("GEMINI_API_KEY not found in environment variables.")

class GeminiClient:
    def __init__(self):
        self.model = genai.GenerativeModel("gemini-3.1-flash-lite")

    async def generate_content(self, prompt: str) -> str:
        """
        Generates content from the Gemini model based on a prompt.
        """
        try:
            response = await self.model.generate_content_async(prompt)
            return response.text
        except Exception as e:
            logger.exception(f"Error calling Gemini AI: {e}")
            raise

gemini_client = GeminiClient()

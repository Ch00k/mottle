from django.conf import settings
from openai import AsyncOpenAI

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


async def generate_cover_image_prompt(album_title: str) -> str:
    await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": (
                    f"I need a cover image for a Spotify playlist titled '{album_title}'. "
                    "The resulting image must have no text on it. "
                    "Help me generate a prompt that I could give to DALL-E. "
                    "Add as many details as you can. "
                    "In your output include only the prompt itself.",
                ),
            }
        ],
    )


client.images.generate(
    prompt='A cover image for a Spotify playlist titled "The Kiffness discography". The image must contain no text.',
    model="dall-e-3",
    quality="standard",
    style="vivid",
    n=1,
    response_format="url",
    size="1024x1024",
)

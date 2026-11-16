import os
from openai import OpenAI

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],
)
resp = client.chat.completions.create(
    model="qwen/qwen3.6-35b-a3b",
    messages=[{"role": "user", "content": "Say hello in one word."}],
)
print(resp.choices[0].message.content)
print(resp.usage)

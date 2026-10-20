import os
import boto3
from dotenv import load_dotenv

load_dotenv()
region = os.getenv("AWS_REGION", "eu-north-1")
runtime = boto3.client("bedrock-runtime", region_name=region)

candidates = [
    "eu.anthropic.claude-haiku-5-5",
    "eu.anthropic.claude-sonnet-5-5",
    "eu.anthropic.claude-sonnet-5",
    "eu.anthropic.claude-sonnet-4-6",
    "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
    "global.anthropic.claude-haiku-5-5",
    "global.anthropic.claude-sonnet-5-5",
]

for model_id in candidates:
    try:
        r = runtime.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "Reply with: ok"}]}],
            inferenceConfig={"maxTokens": 5},
        )
        print("OK  ", model_id, r["metrics"]["latencyMs"], "ms")
    except Exception as e:
        print("FAIL", model_id, type(e).__name__, str(e)[:80])

import os
import boto3
from dotenv import load_dotenv

load_dotenv()
region = os.getenv("AWS_REGION", "eu-north-1")

bedrock = boto3.client("bedrock", region_name=region)
profiles = bedrock.list_inference_profiles()["inferenceProfileSummaries"]
claude = [p["inferenceProfileId"] for p in profiles if "anthropic" in p["inferenceProfileId"]]
for p in claude:
    print(p)

model_id = os.getenv("BEDROCK_MODEL_ID") or claude[-1]
runtime = boto3.client("bedrock-runtime", region_name=region)
r = runtime.converse(
    modelId=model_id,
    messages=[{"role": "user", "content": [{"text": "Reply with exactly: sentinel online"}]}],
    inferenceConfig={"maxTokens": 20},
)
print(model_id, "->", r["output"]["message"]["content"][0]["text"], r["metrics"]["latencyMs"], "ms")

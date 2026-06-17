from __future__ import annotations

import json
from typing import Any

# Titan Text Embeddings v2 returns 1024-dimension vectors.
MODEL_ID = "amazon.titan-embed-text-v2:0"
DIMENSIONS = 1024

# Embeds text via Bedrock Titan. The bedrock-runtime client is injected.
class TitanEmbedder:
    def __init__(self, client: Any, model_id: str = MODEL_ID) -> None:
        self._client = client
        self._model_id = model_id

    def embed(self, text: str) -> list[float]:
        response = self._client.invoke_model(
            modelId=self._model_id,
            body=json.dumps({"inputText": text}),
        )
        payload = json.loads(response["body"].read())
        return payload["embedding"]

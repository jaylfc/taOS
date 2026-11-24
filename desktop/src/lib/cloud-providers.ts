/**
 * Cloud model providers a user can add with just an API key: shared by
 * Providers and first-run setup.
 */
export interface CloudProviderMeta {
  label: string;
  description: string;
  url: string;
  keyPlaceholder: string;
}

export const CLOUD_PROVIDER_META: Record<string, CloudProviderMeta> = {
  openai: {
    label: "OpenAI",
    description: "GPT-4o, o1, and more",
    url: "https://api.openai.com/v1",
    keyPlaceholder: "sk-...",
  },
  anthropic: {
    label: "Anthropic",
    description: "Claude Sonnet, Opus, Haiku",
    url: "https://api.anthropic.com/v1",
    keyPlaceholder: "sk-ant-...",
  },
  openrouter: {
    label: "OpenRouter",
    description: "300+ models via one API",
    url: "https://openrouter.ai/api/v1",
    keyPlaceholder: "sk-or-...",
  },
  kilocode: {
    label: "Kilo",
    description: "500+ models, smart routing",
    url: "https://api.kilo.ai/api/gateway",
    keyPlaceholder: "kilo-...",
  },
  deepseek: {
    label: "DeepSeek",
    description: "DeepSeek V4 Pro, Flash, Reasoner",
    url: "https://api.deepseek.com",
    keyPlaceholder: "sk-...",
  },
  nous: {
    label: "Nous Portal",
    description: "Hermes 4 and frontier models via Nous Research",
    url: "https://inference-api.nousresearch.com/v1",
    keyPlaceholder: "your Portal token",
  },
  "openai-compatible": {
    label: "OpenAI-Compatible",
    description: "LiteLLM, llama.cpp server, vLLM, or any service exposing the OpenAI API",
    url: "",
    keyPlaceholder: "your-api-key",
  },
};

import dotenv from "dotenv";

dotenv.config();

export function getConfig() {
  const baseUrl = process.env.OLLAMA_BASE_URL || "http://127.0.0.1:11434";
  const model = process.env.OLLAMA_MODEL || "llama3.2:3b";

  return { baseUrl, model };
}

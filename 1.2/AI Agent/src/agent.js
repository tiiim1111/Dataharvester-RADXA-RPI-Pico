export class LearningAgent {
  constructor({ baseUrl, model, systemPrompt }) {
    this.baseUrl = baseUrl;
    this.model = model;
    this.systemPrompt = systemPrompt;
    this.history = [];
  }

  async reply(userInput) {
    this.history.push({
      role: "user",
      content: userInput
    });

    const response = await fetch(`${this.baseUrl}/api/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        model: this.model,
        stream: false,
        messages: [
          {
            role: "system",
            content: this.systemPrompt
          },
          ...this.history
        ]
      })
    });

    if (!response.ok) {
      const errorText = await response.text();

      throw new Error(
        `Ollama request failed (${response.status}). ${errorText || "No details returned."}`
      );
    }

    const data = await response.json();
    const text = data.message?.content?.trim() || "Walang nabuong sagot.";

    this.history.push({
      role: "assistant",
      content: text
    });

    return text;
  }
}

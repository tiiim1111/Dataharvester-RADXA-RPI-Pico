import readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import { getConfig } from "./config.js";
import { LearningAgent } from "./agent.js";

async function main() {
  const { baseUrl, model } = getConfig();

  const agent = new LearningAgent({
    baseUrl,
    model,
    systemPrompt: [
      "You are a beginner-friendly AI agent coach.",
      "Explain clearly, keep answers practical, and suggest next steps.",
      "If the user asks for code, provide simple examples first."
    ].join(" ")
  });

  const rl = readline.createInterface({ input, output });

  console.log("AI Agent Crash Course");
  console.log(`Model: ${model}`);
  console.log(`Ollama: ${baseUrl}`);
  console.log("Type your question. Use 'exit' to quit.");

  while (true) {
    const question = (await rl.question("\nIkaw: ")).trim();

    if (!question) {
      continue;
    }

    if (question.toLowerCase() === "exit") {
      break;
    }

    const answer = await agent.reply(question);
    console.log(`\nAgent: ${answer}`);
  }

  rl.close();
}

main().catch((error) => {
  console.error(`\nError: ${error.message}`);
  process.exit(1);
});

# AI Agent Crash Course

Practical starter project ito para matutunan mo kung paano bumuo ng isang simpleng AI agent gamit ang Node.js at Ollama.

## Ano ang mabubuo natin

- CLI-based AI agent
- May memory ng conversation sa kasalukuyang session
- Configurable local model via `.env`
- Malinaw na project structure para madali ang susunod na upgrades

## Project Structure

```text
.
|-- .env.example
|-- package.json
|-- src
|   |-- agent.js
|   |-- config.js
|   `-- index.js
`-- README.md
```

## Step 1: Install dependencies

```powershell
& "C:\Program Files\nodejs\npm.cmd" install
```

## Step 2: Install Ollama

Visit [https://ollama.com/download](https://ollama.com/download) and install the Windows app.

After install, pull a small model:

```powershell
ollama pull llama3.2:3b
```

## Step 3: Set your local config

```powershell
Copy-Item .env.example .env
```

Then edit `.env`:

```env
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.2:3b
```

## Step 4: Run the agent

```powershell
& "C:\Program Files\nodejs\npm.cmd" start
```

## How this agent works

1. `src/config.js`
   Loads the Ollama base URL and model name.
2. `src/agent.js`
   Sends chat requests to Ollama and keeps the chat history in memory.
3. `src/index.js`
   Runs a command-line loop so you can chat with the agent.

## Zero to Hero Roadmap

### Level 1: Working Agent

- Understand `systemPrompt`
- Understand `history`
- Understand `data.message.content`

### Level 2: Better Memory

- Save conversation to a file
- Add session IDs
- Load previous chats

### Level 3: Tools

- Add calculator tool
- Add file reader tool
- Let the agent choose when to use a tool

### Level 4: Real Automation

- Connect to your harvester scripts
- Feed real input files
- Generate summaries and actions automatically

## Suggested next upgrades

- Add markdown logging
- Add retry handling
- Add tool calling
- Add streaming output
- Add a web dashboard later

## Quick challenge

Pag gumagana na ito, ang next nating magandang move ay:

1. Bigyan ang agent ng custom role para sa harvester workflow mo.
2. Turuan itong magbasa ng local files.
3. Ipagawa sa kanya ang summarization o data cleanup.

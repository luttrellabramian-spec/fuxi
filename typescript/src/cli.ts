#!/usr/bin/env node
/** CLI 对话窗口 - 持续对话模式 */
import * as readline from 'readline';
import axios from 'axios';

try {
  const dotenv = require("dotenv");
  dotenv.config();
} catch(e) {}

const HTTP_PORT = parseInt(process.env.HTTP_PORT || "18789", 10);
const MODEL = process.env.DEFAULT_MODEL || "";

const session_id = `cli-${Date.now()}`;
const history: Array<{role: string, content: string}> = [];
const MAX_HISTORY = 100;
let multilineBuffer = "";
let isMultiline = false;

// P2-1: 命令历史（用于 Tab 补全和搜索）
const commandHistory: string[] = [];
let historyIndex = -1;

// P2-1: 可补全的命令列表
const COMMANDS = ["help", "clear", "exit", "quit", "status", "set", "history", "search"];

const runtimeState = {
  model: "",
  apiKey: "",
  baseUrl: "",
};

function checkFirstRun(): boolean {
  return !process.env.LLM_API_KEY && !runtimeState.apiKey;
}

async function firstTimeSetup(): Promise<void> {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  const ask = (q: string): Promise<string> => new Promise(res => rl.question(q, ans => res(ans)));

  console.log('\n================================================');
  console.log(' 伏羲 - 首次配置');
  console.log('================================================\n');
  console.log('检测到未配置 API Key，请输入以下信息：\n');

  const apiKey = await ask('API Key: ');
  if (!apiKey.trim()) {
    console.error('错误：API Key 不能为空');
    rl.close();
    process.exit(1);
  }

  const baseUrl = await ask('Base URL (如 https://api.minimax.chat/v1): ');
  const model = await ask('模型 (如 MiniMax-M2.7): ');

  runtimeState.apiKey = apiKey.trim();
  runtimeState.baseUrl = baseUrl.trim();
  runtimeState.model = model.trim();

  console.log('\n✓ 配置完成！\n');
  console.log(`  模型: ${runtimeState.model}`);
  console.log(`  Base URL: ${runtimeState.baseUrl}`);
  console.log(`  API Key: ***${runtimeState.apiKey.slice(-4)}`);
  console.log('\n提示：使用 "set" 命令可随时修改配置\n');

  rl.close();
}

function printHelp() {
  console.log(`
可用命令：
  help       - 显示此帮助信息
  clear      - 清空对话历史
  exit/quit  - 退出程序
  status     - 显示当前会话状态
  set        - 修改配置（模型、API Key、URL）

直接输入内容即可对话，换行输入 \\ 可继续输入多行。
`);
}

function clearHistory() {
  history.length = 0;
  console.log('对话历史已清空。\n');
}

function printStatus() {
  console.log(`
会话 ID: ${session_id}
历史条数: ${history.length}
模型: ${runtimeState.model || "（默认）"}
API 地址: http://localhost:${HTTP_PORT}
Base URL: ${runtimeState.baseUrl || "（默认）"}
API Key: ${runtimeState.apiKey ? '***' + runtimeState.apiKey.slice(-4) : "（未配置）"}
`);
}

async function main() {
  // 单次命令行模式：node cli.js "你好"
  const args = process.argv.slice(2);
  if (args.length > 0) {
    const message = args.join(' ');
    const session_id = `cli-once-${Date.now()}`;
    try {
      const headers: Record<string, string> = {};
      const apiKey = process.env.LLM_API_KEY || '';
      const baseUrl = process.env.LLM_BASE_URL || '';
      if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`;
      if (baseUrl) headers['X-Base-Url'] = baseUrl;

      const payload: any = { message, session_id };
      const model = process.env.DEFAULT_MODEL || process.env.LLM_MODEL || '';
      if (model) payload.model = model;

      const axiosConfig: any = { timeout: 60000 };
      if (Object.keys(headers).length > 0) axiosConfig.headers = headers;

      const resp = await axios.post(`http://localhost:${HTTP_PORT}/chat`, payload, axiosConfig);
      const reply = resp.data?.data?.content || resp.data?.error || '（无回复）';
      console.log(reply);
      process.exit(0);
    } catch (err: any) {
      console.error('Error:', err.message);
      if (err.response) {
        console.error('服务端:', err.response.data?.error || err.response.statusText);
      }
      process.exit(1);
    }
    return;
  }

  if (checkFirstRun()) {
    await firstTimeSetup();
  }

  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    prompt: '伏羲 > ',
  });

  console.log('\n================================================');
  console.log(' 伏羲 CLI 对话窗口 - 持续对话模式');
  console.log(' 输入 "help" 查看命令，"exit" 退出');
  console.log('================================================\n');

  // P2-1: Tab 补全器
  const completer = (line: string): [string[], string] => {
    const hits = COMMANDS.filter((cmd) => cmd.startsWith(line.toLowerCase()));
    return [hits.length ? hits : [], line];
  };

  async function interactiveSet() {
    const rl2 = readline.createInterface({ input: process.stdin, output: process.stdout });
    const ask = (q: string): Promise<string> => new Promise(res => rl2.question(q, ans => res(ans)));

    console.log("\n--- 设置 ---");
    const model = await ask(`模型 [${runtimeState.model || MODEL}]: `);
    const baseUrl = await ask(`Base URL [${runtimeState.baseUrl || '（空）'}]: `);
    const apiKey = await ask(`API Key [***]: `);

    if (model.trim()) runtimeState.model = model.trim();
    if (baseUrl.trim()) runtimeState.baseUrl = baseUrl.trim();
    if (apiKey.trim()) runtimeState.apiKey = apiKey.trim();

    console.log("\n✓ 已更新，当前配置：");
    console.log(`  模型: ${runtimeState.model || MODEL}`);
    console.log(`  Base URL: ${runtimeState.baseUrl || '（默认）'}`);
    console.log(`  API Key: ${runtimeState.apiKey ? '***' + runtimeState.apiKey.slice(-4) : '（默认）'}`);

    rl2.close();
    rl.setPrompt("伏羲 > ");
  }

  function handleCommand(cmd: string): boolean {
    const trimmed = cmd.trim().toLowerCase();
    if (trimmed === 'help') {
      printHelp();
      return true;
    } else if (trimmed === 'clear') {
      clearHistory();
      return true;
    } else if (trimmed === 'status') {
      printStatus();
      return true;
    } else if (trimmed === 'set') {
      interactiveSet();
      return true;
    } else if (trimmed === 'exit' || trimmed === 'quit') {
      console.log('\n再见！');
      rl.close();
      process.exit(0);
    }
    return false;
  }

  async function sendMessage(content: string): Promise<void> {
    try {
      const payload: any = { message: content, session_id };
      if (runtimeState.model) payload.model = runtimeState.model;

      const headers: any = {};
      if (runtimeState.apiKey) headers['Authorization'] = `Bearer ${runtimeState.apiKey}`;
      if (runtimeState.baseUrl) headers['X-Base-Url'] = runtimeState.baseUrl;

      const resp = await axios.post(`http://localhost:${HTTP_PORT}/chat`, payload, {
        headers: Object.keys(headers).length ? headers : undefined,
        timeout: 60000,
      });

      const reply = resp.data?.data?.content || resp.data?.error || '（无回复）';
      history.push({ role: 'user', content });
      history.push({ role: 'assistant', content: reply });
      if (history.length > MAX_HISTORY) {
        history.splice(0, history.length - MAX_HISTORY);
      }

      console.log('\n伏羲:', reply);
    } catch (err: any) {
      console.error('\n错误:', err.message);
      if (err.response) {
        console.error('服务端响应:', err.response.data?.error || err.response.statusText);
      }
    }
  }

  async function processInput(line: string): Promise<void> {
    const trimmed = line.trim();

    if (!trimmed && !isMultiline) {
      rl.prompt();
      return;
    }

    if (isMultiline) {
      if (trimmed.endsWith('\\')) {
        multilineBuffer += trimmed.slice(0, -1) + '\n';
      } else {
        multilineBuffer += trimmed;
        isMultiline = false;
        const content = multilineBuffer.trim();
        multilineBuffer = "";
        if (content) {
          await sendMessage(content);
        }
      }
      rl.prompt();
      return;
    }

    if (trimmed.endsWith('\\')) {
      multilineBuffer = trimmed.slice(0, -1);
      isMultiline = true;
      rl.setPrompt('... ');
      rl.prompt();
      return;
    }

    rl.setPrompt('伏羲 > ');

    if (trimmed && !handleCommand(trimmed)) {
      await sendMessage(trimmed);
    }

    rl.prompt();
  }

  rl.on('line', (line) => {
    processInput(line).catch((err) => console.error('[CLI Error]', err));
  });

  rl.on('close', () => {
    console.log('\n再见！');
    process.exit(0);
  });

  rl.prompt();
}

main().catch(console.error);
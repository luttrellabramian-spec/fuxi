/** v0.2.6 (H1) — WebSocket /ws/chat 处理
 *
 * 接受浏览器客户端通过 WebSocket 发起的 chat 消息（流式响应）。
 */
import ws from "ws";
import http from "http";
import { buildMetadata } from "../helpers";
import { getRequestId } from "../middleware/requestId";
import { RouteContext, ProtoChunk } from "../types";

const wsSessions = new Map<string, ws.WebSocket>();

export function attachChatSocket(server: http.Server, ctx: RouteContext): ws.Server {
  const { runtimeConfig, grpcClient, fuxiProto } = ctx;
  const wss = new ws.Server({ server, path: "/ws/chat" });

  wss.on("connection", (ws: ws.WebSocket, req: http.IncomingMessage) => {
    const url = new URL(req.url || "/", `http://${req.headers.host}`);
    const sessionId = url.searchParams.get("session_id") || `ws-${Date.now()}`;
    const requestId = getRequestId(req);
    wsSessions.set(sessionId, ws);
    console.log(`[WS] Client connected: session=${sessionId}, request_id=${requestId}`);

    ws.on("message", async (data: ws.Data) => {
      try {
        const msg = JSON.parse(data.toString());
        if (msg.type === "chat") {
          const { message, model } = msg;
          if (!message) {
            ws.send(JSON.stringify({ error: "message is required" }));
            return;
          }

          const metadata = buildMetadata(req, runtimeConfig, { model });
          const reqMsg = new fuxiProto.CompletionRequest();
          reqMsg.setSessionId(sessionId);
          reqMsg.setUserMessage(message);
          if (model) reqMsg.setModel(model);
          reqMsg.setMaxTokens(runtimeConfig.maxTokens || 4096);

          const deadline = new Date();
          deadline.setSeconds(deadline.getSeconds() + 60);

          const call = grpcClient.streamComplete(reqMsg, metadata, { deadline });
          let fullContent = "";

          // proto-loader 生成的回调类型是 any；用 ProtoChunk 收窄访问
          call.on("data", (chunk: ProtoChunk | unknown) => {
            const c = chunk as ProtoChunk;
            const content = c.getContent ? c.getContent() : c.content;
            const is_final = c.getIsFinal ? c.getIsFinal() : c.is_final;
            fullContent += content || "";
            ws.send(JSON.stringify({ type: "token", content, is_final: !!is_final }));
          });

          call.on("end", () => {
            ws.send(JSON.stringify({ type: "done", content: fullContent }));
          });

          call.on("error", (err: { message?: string } | unknown) => {
            const e = err as { message?: string };
            ws.send(JSON.stringify({ type: "error", error: e.message || String(err) }));
          });
        } else if (msg.type === "ping") {
          ws.send(JSON.stringify({ type: "pong", timestamp: Date.now() }));
        }
      } catch (e: unknown) {
        const err = e as { message?: string };
        ws.send(JSON.stringify({ type: "error", error: err.message || String(e) }));
      }
    });

    ws.on("close", () => {
      wsSessions.delete(sessionId);
      console.log(`[WS] Client disconnected: session=${sessionId}`);
    });

    ws.on("error", (err: Error) => {
      console.error(`[WS] Error for ${sessionId}:`, err.message);
    });
  });

  return wss;
}

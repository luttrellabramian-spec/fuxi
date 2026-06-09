/** v0.2.6 (H1) — /chat 与 /chat/stream 路由
 *
 * 从 gateway.ts 拆出。注册方式：registerChatRoutes(app, ctx)
 */
import express from "express";
import { catchAsync, wrapResponse, stripThinkTagsInPlace } from "../middleware/asyncHandler";
import { buildMetadata, logger, metrics } from "../helpers";
import { RouteContext } from "../types";

export function registerChatRoutes(app: express.Express, ctx: RouteContext): void {
  const { runtimeConfig, grpcClient, fuxiProto, config } = ctx;

  /** POST /chat - 聊天对话（同步累积模式） */
  app.post(
    "/chat",
    catchAsync(async (req: express.Request, res: express.Response) => {
      const { message, session_id = "default", model: bodyModel = "" } = req.body;
      if (!message) {
        return wrapResponse(res, false, null, "message is required");
      }

      const model = bodyModel || runtimeConfig.model || config.auth.model || "";
      const metadata = buildMetadata(req, runtimeConfig, { model });

      const deadline = new Date();
      deadline.setSeconds(deadline.getSeconds() + 60);

      const reqMsg = new fuxiProto.CompletionRequest();
      reqMsg.setSessionId(session_id);
      reqMsg.setUserMessage(message);
      if (model) reqMsg.setModel(model);
      reqMsg.setMaxTokens(runtimeConfig.maxTokens || 4096);

      const call = grpcClient.streamComplete(reqMsg, metadata, { deadline: deadline });

      let fullContent = "";
      let responded = false;

      call.on("data", (chunk: unknown) => {
        const c = chunk as { getContent?: () => string; getIsFinal?: () => boolean; content?: string; is_final?: boolean };
        const content = c.getContent ? c.getContent() : c.content;
        const is_final = c.getIsFinal ? c.getIsFinal() : c.is_final;
        fullContent += content || "";
        if (is_final && !responded) {
          responded = true;
          const finalContent = content || fullContent;
          const cleaned = stripThinkTagsInPlace(finalContent);
          wrapResponse(res, true, { content: cleaned, model: model || '当前模型' });
        }
      });

      call.on("end", () => {
        if (!responded) {
          responded = true;
          const cleaned = stripThinkTagsInPlace(fullContent);
          wrapResponse(res, true, { content: cleaned, model: model || '当前模型' });
        }
      });

      call.on("error", (err: unknown) => {
        if (!responded) {
          responded = true;
          const e = err as { message?: string };
          wrapResponse(res, false, null, e.message);
        }
      });
    })
  );

  /** POST /chat/stream - 流式聊天对话（SSE） */
  app.post("/chat/stream", catchAsync(async (req: express.Request, res: express.Response) => {
    const { message, session_id = "default", model: bodyModel = "" } = req.body;
    if (!message) {
      res.status(400).json({ ok: false, error: "message is required" });
      return;
    }

    const model = bodyModel || runtimeConfig.model || config.auth.model || "";
    const metadata = buildMetadata(req, runtimeConfig, { model });

    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
      'X-Accel-Buffering': 'no',
    });

    const reqMsg = new fuxiProto.CompletionRequest();
    reqMsg.setSessionId(session_id);
    reqMsg.setUserMessage(message);
    if (model) reqMsg.setModel(model);
    reqMsg.setMaxTokens(runtimeConfig.maxTokens || 4096);

    const deadline = new Date();
    deadline.setSeconds(deadline.getSeconds() + 60);

    const call = grpcClient.streamComplete(reqMsg, metadata, { deadline: deadline });
    let hasSentData = false;
    let fullContent = "";

    call.on("data", (chunk: unknown) => {
      const c = chunk as { getContent?: () => string; getIsFinal?: () => boolean; content?: string; is_final?: boolean };
      const rawContent = c.getContent ? c.getContent() : c.content;
      const is_final = c.getIsFinal ? c.getIsFinal() : c.is_final;

      if (rawContent) {
        const content = stripThinkTagsInPlace(rawContent);
        fullContent += rawContent;
        if (content) {
          hasSentData = true;
          res.write(`data: ${JSON.stringify({ content, is_final: !!is_final })}\n\n`);
        }
      }
    });

    call.on("end", () => {
      if (!hasSentData) {
        res.write(`data: ${JSON.stringify({ content: "", is_final: true })}\n\n`);
      } else {
        const cleaned = stripThinkTagsInPlace(fullContent);
        if (cleaned !== fullContent) {
          logger.warn(
            `[/chat/stream] think 标签跨 chunk 残留，${fullContent.length - cleaned.length} 字符被剥离`
          );
        }
      }
      res.write('data: [DONE]\n\n');
      res.end();
    });

    call.on("error", (err: unknown) => {
      const e = err as { message?: string };
      let errorMsg = e.message || "未知错误";
      if (errorMsg.includes("DEADLINE_EXCEEDED")) {
        errorMsg = "服务响应超时，请稍后重试";
      } else if (errorMsg.includes("UNAVAILABLE")) {
        errorMsg = "服务暂时不可用";
      }
      res.write(`data: ${JSON.stringify({ error: errorMsg, is_final: true })}\n\n`);
      res.write('data: [DONE]\n\n');
      res.end();
    });

    req.on("close", () => {
      try { call.cancel(); } catch (e) { /* ignore */ }
    });
  }));
}

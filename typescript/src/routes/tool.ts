/** v0.2.6 (H1) — /tool/list 与 /tool/invoke 路由
 */
import express from "express";
import { catchAsync, wrapResponse } from "../middleware/asyncHandler";
import { buildMetadata, logger } from "../helpers";
import { RouteContext } from "../types";

export function registerToolRoutes(app: express.Express, ctx: RouteContext): void {
  const { runtimeConfig, grpcClient, fuxiProto, config } = ctx;

  /** POST /tool/invoke - 调用工具 */
  app.post(
    "/tool/invoke",
    catchAsync(async (req: express.Request, res: express.Response) => {
      const { tool_name, arguments: args, session_id = "default", model } = req.body;
      if (!tool_name) {
        return wrapResponse(res, false, null, "tool_name is required");
      }

      const metadata = buildMetadata(req, runtimeConfig);

      const resolvedToolModel = model || runtimeConfig.model || config.auth.model || "";
      if (resolvedToolModel) {
        metadata.add('model', resolvedToolModel);
      }

      const deadline = new Date();
      deadline.setSeconds(deadline.getSeconds() + 15);

      const toolReqMsg = new fuxiProto.ToolRequest();
      toolReqMsg.setToolName(tool_name);
      toolReqMsg.setArgumentsJson(JSON.stringify(args || {}));
      toolReqMsg.setSessionId(session_id);
      if (resolvedToolModel) toolReqMsg.setModel(resolvedToolModel);

      grpcClient.invokeTool(toolReqMsg, metadata, { deadline: deadline }, (error: unknown, response: unknown) => {
        if (error) {
          const e = error as { message?: string };
          logger.error("InvokeTool error:", e);
          return wrapResponse(res, false, null, e.message);
        }
        const r = response as { getResultJson?: () => string; getSuccess?: () => boolean; getElapsedMs?: () => number | string; getError?: () => string; result_json?: string; success?: boolean; elapsed_ms?: number | string; error?: string };
        let result: any = {};
        try {
          const resultJson = r.getResultJson ? r.getResultJson() : r.result_json;
          result = resultJson ? JSON.parse(resultJson) : {};
        } catch (e) {
          const resultJson = r.getResultJson ? r.getResultJson() : r.result_json;
          result = { raw: resultJson };
        }
        const respSuccess = Boolean(r.getSuccess ? r.getSuccess() : r.success);
        const respElapsed = r.getElapsedMs ? r.getElapsedMs() : r.elapsed_ms;
        const respError = r.getError ? r.getError() : r.error;
        wrapResponse(res, respSuccess, { result, elapsed_ms: Number(respElapsed) }, respError);
      });
    })
  );

  /** GET /tool/list - 列出所有可用工具 */
  app.get(
    "/tool/list",
    catchAsync(async (req: express.Request, res: express.Response) => {
      const fallbackTools = [
        { name: "read_file", level: "L0", desc: "读取文件内容" },
        { name: "write_file", level: "L1", desc: "写入文件内容" },
        { name: "list_files", level: "L0", desc: "列出目录下文件" },
        { name: "file_exists", level: "L0", desc: "检查文件是否存在" },
        { name: "read_json", level: "L0", desc: "读取 JSON 文件" },
        { name: "write_json", level: "L1", desc: "写入 JSON 文件" },
        { name: "check_url", level: "L0", desc: "检查 URL 可达性" },
        { name: "grep", level: "L0", desc: "在文件中搜索文本" },
        { name: "search_replace", level: "L1", desc: "搜索并替换文本" },
        { name: "search_file", level: "L0", desc: "按模式搜索文件" },
        { name: "memory_write", level: "L1", desc: "写入记忆（hot/warm/cold）" },
        { name: "memory_query", level: "L0", desc: "查询记忆" },
        { name: "memory_get_recent", level: "L0", desc: "获取最近记忆" },
      ];
      wrapResponse(res, true, { tools: fallbackTools, source: "cache" });
    })
  );
}

/** v0.2.6 (H1) — /memory/* 路由（8 个）
 *
 * 包含 hot / warm / cold 三种记忆的读和写。
 */
import express from "express";
import { catchAsync, wrapResponse } from "../middleware/asyncHandler";
import { buildMetadata, logger } from "../helpers";
import { RouteContext } from "../types";

export function registerMemoryRoutes(app: express.Express, ctx: RouteContext): void {
  const { runtimeConfig, memoryClient, fuxiProto } = ctx;

  /** GET /memory/hot - 查询热记忆 */
  app.get(
    "/memory/hot",
    catchAsync(async (req: express.Request, res: express.Response) => {
      const session_id = (req.query.session_id as string) || "default";
      const deadline = new Date();
      deadline.setSeconds(deadline.getSeconds() + 5);
      const hotQueryMsg = new fuxiProto.HotQuery();
      hotQueryMsg.setSessionId(session_id);
      memoryClient.queryHot(hotQueryMsg, buildMetadata(req, runtimeConfig), { deadline: deadline }, (err: unknown, response: unknown) => {
        if (err) {
          logger.error("QueryHot error:", err);
          return wrapResponse(res, false, null, (err as { message?: string }).message);
        }
const r = response as { getSuccess?: () => boolean; getId?: () => string; getError?: () => string; getMemoryContent?: () => string; getCharCount?: () => number; getEntriesList?: () => any[]; getMemoriesList?: () => any[]; success?: boolean; id?: string; error?: string; memory_content?: string; char_count?: number; entries?: any[]; memories?: any[] };
                const memContent = typeof r.getMemoryContent === 'function' ? r.getMemoryContent() : r.memory_content;
        const charCount = typeof r.getCharCount === 'function' ? r.getCharCount() : r.char_count;
        wrapResponse(res, true, { content: memContent, char_count: charCount });
      });
    })
  );

  /** POST /memory/hot - 写入热记忆 */
  app.post(
    "/memory/hot",
    catchAsync(async (req: express.Request, res: express.Response) => {
      const { content, session_id = "default" } = req.body;
      if (!content) return wrapResponse(res, false, null, "content is required");
      const deadline = new Date();
      deadline.setSeconds(deadline.getSeconds() + 5);
      const memWriteMsg = new fuxiProto.MemoryWrite();
      memWriteMsg.setMemoryType("hot");
      memWriteMsg.setContent(content);
      memWriteMsg.setSessionId(session_id);
      memoryClient.persistMemory(memWriteMsg, buildMetadata(req, runtimeConfig), { deadline: deadline }, (err: unknown, response: unknown) => {
        if (err) {
          logger.error("PersistMemory hot error:", err);
          return wrapResponse(res, false, null, (err as { message?: string }).message);
        }
const r = response as { getSuccess?: () => boolean; getId?: () => string; getError?: () => string; getMemoryContent?: () => string; getCharCount?: () => number; getEntriesList?: () => any[]; getMemoriesList?: () => any[]; success?: boolean; id?: string; error?: string; memory_content?: string; char_count?: number; entries?: any[]; memories?: any[] };
                const respSuccess = Boolean(typeof r.getSuccess === 'function' ? r.getSuccess() : r.success);
        const respId = typeof r.getId === 'function' ? r.getId() : r.id;
        const respError = typeof r.getError === 'function' ? r.getError() : r.error;
        wrapResponse(res, respSuccess, { id: respId }, respError);
      });
    })
  );

  /** POST /memory/warm/add */
  app.post(
    "/memory/warm/add",
    catchAsync(async (req: express.Request, res: express.Response) => {
      const { content, session_id = "default" } = req.body;
      if (!content) return wrapResponse(res, false, null, "content is required");
      const deadline = new Date();
      deadline.setSeconds(deadline.getSeconds() + 5);
      const msg = new fuxiProto.MemoryWrite();
      msg.setMemoryType("warm");
      msg.setContent(content);
      msg.setSessionId(session_id);
      memoryClient.persistMemory(msg, buildMetadata(req, runtimeConfig), { deadline: deadline }, (err: unknown, response: unknown) => {
        if (err) {
          logger.error("PersistMemory warm error:", err);
          return wrapResponse(res, false, null, (err as { message?: string }).message);
        }
        const r = response as { getSuccess?: () => boolean; getId?: () => string; getError?: () => string; success?: boolean; id?: string; error?: string };
        wrapResponse(
          res,
          Boolean(typeof r.getSuccess === 'function' ? r.getSuccess() : r.success),
          { id: typeof r.getId === 'function' ? r.getId() : r.id },
          typeof r.getError === 'function' ? r.getError() : r.error
        );
      });
    })
  );

  /** GET /memory/warm/recent */
  app.get(
    "/memory/warm/recent",
    catchAsync(async (req: express.Request, res: express.Response) => {
      const session_id = (req.query.session_id as string) || "default";
      const limit = parseInt(req.query.limit as string) || 50;
      const deadline = new Date();
      deadline.setSeconds(deadline.getSeconds() + 5);
      const warmQueryMsg = new fuxiProto.WarmQuery();
      warmQueryMsg.setSessionId(session_id);
      warmQueryMsg.setLimit(limit);
      memoryClient.queryWarm(warmQueryMsg, buildMetadata(req, runtimeConfig), { deadline: deadline }, (err: unknown, response: unknown) => {
        if (err) {
          logger.error("QueryWarm error:", err);
          return wrapResponse(res, false, null, (err as { message?: string }).message);
        }
const r = response as { getSuccess?: () => boolean; getId?: () => string; getError?: () => string; getMemoryContent?: () => string; getCharCount?: () => number; getEntriesList?: () => any[]; getMemoriesList?: () => any[]; success?: boolean; id?: string; error?: string; memory_content?: string; char_count?: number; entries?: any[]; memories?: any[] };
                const entries = (r.getEntriesList ? r.getEntriesList() : r.entries || []).map((e: unknown) => {
          const entry = e as { getId?: () => string; getContent?: () => string; getTimestamp?: () => number; id?: string; content?: string; timestamp?: number };
          return { id: typeof entry.getId === 'function' ? entry.getId() : entry.id,
          content: typeof entry.getContent === 'function' ? entry.getContent() : entry.content,
          timestamp: typeof entry.getTimestamp === 'function' ? entry.getTimestamp() : entry.timestamp, };
        });
        wrapResponse(res, true, { entries });
      });
    })
  );

  /** GET /memory/warm/search */
  app.get(
    "/memory/warm/search",
    catchAsync(async (req: express.Request, res: express.Response) => {
      const session_id = (req.query.session_id as string) || "default";
      const query = (req.query.query as string) || "";
      const limit = parseInt(req.query.limit as string) || 10;
      if (!query) return wrapResponse(res, false, null, "query parameter is required");
      const deadline = new Date();
      deadline.setSeconds(deadline.getSeconds() + 5);
      const warmSearchMsg = new fuxiProto.WarmQuery();
      warmSearchMsg.setSessionId(session_id);
      warmSearchMsg.setQuery(query);
      warmSearchMsg.setLimit(limit);
      memoryClient.queryWarm(warmSearchMsg, buildMetadata(req, runtimeConfig), { deadline: deadline }, (err: unknown, response: unknown) => {
        if (err) {
          logger.error("QueryWarm search error:", err);
          return wrapResponse(res, false, null, (err as { message?: string }).message);
        }
const r = response as { getSuccess?: () => boolean; getId?: () => string; getError?: () => string; getMemoryContent?: () => string; getCharCount?: () => number; getEntriesList?: () => any[]; getMemoriesList?: () => any[]; success?: boolean; id?: string; error?: string; memory_content?: string; char_count?: number; entries?: any[]; memories?: any[] };
                const entries = (r.getEntriesList ? r.getEntriesList() : r.entries || []).map((e: unknown) => {
          const entry = e as { getId?: () => string; getContent?: () => string; getTimestamp?: () => number; id?: string; content?: string; timestamp?: number };
          return { id: typeof entry.getId === 'function' ? entry.getId() : entry.id,
          content: typeof entry.getContent === 'function' ? entry.getContent() : entry.content,
          timestamp: typeof entry.getTimestamp === 'function' ? entry.getTimestamp() : entry.timestamp, };
        });
        wrapResponse(res, true, { entries });
      });
    })
  );

  /** POST /memory/cold/add */
  app.post(
    "/memory/cold/add",
    catchAsync(async (req: express.Request, res: express.Response) => {
      const { content, summary, session_id = "default", metadata = {} } = req.body;
      if (!content || !summary) return wrapResponse(res, false, null, "content and summary are required");
      const deadline = new Date();
      deadline.setSeconds(deadline.getSeconds() + 5);
      const memWriteColdMsg = new fuxiProto.MemoryWrite();
      memWriteColdMsg.setMemoryType("cold");
      memWriteColdMsg.setContent(content);
      memWriteColdMsg.setSessionId(session_id);
      if (summary) memWriteColdMsg.setSummary(summary);
      if (metadata && typeof metadata === 'object') {
        const metaMap = memWriteColdMsg.getMetadataMap ? memWriteColdMsg.getMetadataMap() : null;
        if (metaMap) {
          Object.entries(metadata).forEach(([k, v]) => {
            if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') {
              metaMap.set(k, String(v));
            }
          });
        }
      }
      memoryClient.persistMemory(memWriteColdMsg, buildMetadata(req, runtimeConfig), { deadline: deadline }, (err: unknown, response: unknown) => {
        if (err) {
          logger.error("PersistMemory cold error:", err);
          return wrapResponse(res, false, null, (err as { message?: string }).message);
        }
const r = response as { getSuccess?: () => boolean; getId?: () => string; getError?: () => string; getMemoryContent?: () => string; getCharCount?: () => number; getEntriesList?: () => any[]; getMemoriesList?: () => any[]; success?: boolean; id?: string; error?: string; memory_content?: string; char_count?: number; entries?: any[]; memories?: any[] };
                wrapResponse(
          res,
          Boolean(typeof r.getSuccess === 'function' ? r.getSuccess() : r.success),
          { id: typeof r.getId === 'function' ? r.getId() : r.id },
          typeof r.getError === 'function' ? r.getError() : r.error
        );
      });
    })
  );

  /** GET /memory/cold/recent */
  app.get(
    "/memory/cold/recent",
    catchAsync(async (req: express.Request, res: express.Response) => {
      const session_id = (req.query.session_id as string) || "default";
      const limit = parseInt(req.query.limit as string) || 10;
      const deadline = new Date();
      deadline.setSeconds(deadline.getSeconds() + 5);
      const coldQueryMsg = new fuxiProto.ColdQuery();
      coldQueryMsg.setSessionId(session_id);
      coldQueryMsg.setLimit(limit);
      memoryClient.queryCold(coldQueryMsg, buildMetadata(req, runtimeConfig), { deadline: deadline }, (err: unknown, response: unknown) => {
        if (err) {
          logger.error("QueryCold error:", err);
          return wrapResponse(res, false, null, (err as { message?: string }).message);
        }
const r = response as { getSuccess?: () => boolean; getId?: () => string; getError?: () => string; getMemoryContent?: () => string; getCharCount?: () => number; getEntriesList?: () => any[]; getMemoriesList?: () => any[]; success?: boolean; id?: string; error?: string; memory_content?: string; char_count?: number; entries?: any[]; memories?: any[] };
                const memories = (r.getMemoriesList ? r.getMemoriesList() : r.memories || []).map((m: unknown) => {
          const entry = m as { getId?: () => string; getContent?: () => string; getSimilarity?: () => number; id?: string; content?: string; similarity?: number };
          return {
            id: entry.getId ? entry.getId() : entry.id,
            content: entry.getContent ? entry.getContent() : entry.content,
            similarity: entry.getSimilarity ? entry.getSimilarity() : entry.similarity,
          };
        });
        wrapResponse(res, true, { memories });
      });
    })
  );

  /** GET /memory/cold/search */
  app.get(
    "/memory/cold/search",
    catchAsync(async (req: express.Request, res: express.Response) => {
      const query = (req.query.query as string) || "";
      const session_id = (req.query.session_id as string) || "default";
      const limit = parseInt(req.query.limit as string) || 10;
      if (!query) return wrapResponse(res, false, null, "query parameter is required");
      const deadline = new Date();
      deadline.setSeconds(deadline.getSeconds() + 10);
      const coldSearchMsg = new fuxiProto.ColdQuery();
      coldSearchMsg.setSessionId(session_id);
      coldSearchMsg.setQuery(query);
      coldSearchMsg.setLimit(limit);
      memoryClient.queryCold(coldSearchMsg, buildMetadata(req, runtimeConfig), { deadline: deadline }, (err: unknown, response: unknown) => {
        if (err) {
          logger.error("QueryCold search error:", err);
          return wrapResponse(res, false, null, (err as { message?: string }).message);
        }
const r = response as { getSuccess?: () => boolean; getId?: () => string; getError?: () => string; getMemoryContent?: () => string; getCharCount?: () => number; getEntriesList?: () => any[]; getMemoriesList?: () => any[]; success?: boolean; id?: string; error?: string; memory_content?: string; char_count?: number; entries?: any[]; memories?: any[] };
                const memories = (r.getMemoriesList ? r.getMemoriesList() : r.memories || []).map((m: unknown) => {
          const entry = m as { getId?: () => string; getContent?: () => string; getSimilarity?: () => number; id?: string; content?: string; similarity?: number };
          return {
            id: entry.getId ? entry.getId() : entry.id,
            content: entry.getContent ? entry.getContent() : entry.content,
            similarity: entry.getSimilarity ? entry.getSimilarity() : entry.similarity,
          };
        });
        wrapResponse(res, true, { memories });
      });
    })
  );
}

import Anthropic from "@anthropic-ai/sdk";
import { Client as McpClient } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { getCitationData } from "./queries";
import type { AskBlock, AskResponse } from "./types";

/**
 * Live ask mode: Anthropic tool-use loop with the Senedd MCP server's tools
 * (PRD §3 — the LLM path goes through MCP; direct SQL is for search/transcript).
 *
 * Citations: the system prompt tells the model to cite as [speech:ID] inline.
 * We collect those ids, resolve their metadata from the DB, and rewrite the
 * markers into numbered footnotes with citation cards (PRD §5, format A).
 */

const SYSTEM_PROMPT = `You are a research assistant over the Senedd (Welsh Parliament) plenary record.
Use the available tools to find speeches, votes and written answers relevant to the user's question.
Ground every claim in retrieved material. Cite each supporting speech inline as [speech:ID]
(e.g. [speech:1234]) immediately after the claim it supports — use the numeric speech_id from
tool results. Only cite speech ids that appeared in tool results. Keep answers to 2–4 short
paragraphs. If the record contains nothing relevant, say so plainly.`;

const MAX_TOOL_ROUNDS = 8;

interface McpToolDef {
  name: string;
  description?: string;
  inputSchema: Record<string, unknown>;
}

async function connectMcp(url: string): Promise<McpClient> {
  const client = new McpClient({ name: "senedd-web", version: "0.1.0" });
  await client.connect(new StreamableHTTPClientTransport(new URL(url)));
  return client;
}

export async function liveAsk(question: string): Promise<AskResponse> {
  const mcpUrl = process.env.SENEDD_MCP_URL!;
  const anthropic = new Anthropic(); // ANTHROPIC_API_KEY from env
  const model = process.env.ANTHROPIC_MODEL ?? "claude-opus-4-8";

  const mcp = await connectMcp(mcpUrl);
  try {
    const { tools: mcpTools } = (await mcp.listTools()) as { tools: McpToolDef[] };
    const tools: Anthropic.Tool[] = mcpTools.map((t) => ({
      name: t.name,
      description: t.description ?? "",
      input_schema: t.inputSchema as Anthropic.Tool.InputSchema,
    }));

    const messages: Anthropic.MessageParam[] = [{ role: "user", content: question }];
    let response = await anthropic.messages.create({
      model,
      max_tokens: 8192,
      system: SYSTEM_PROMPT,
      tools,
      messages,
    });

    let rounds = 0;
    while (response.stop_reason === "tool_use" && rounds < MAX_TOOL_ROUNDS) {
      rounds += 1;
      const toolUses = response.content.filter(
        (b): b is Anthropic.ToolUseBlock => b.type === "tool_use",
      );
      messages.push({ role: "assistant", content: response.content });

      const results: Anthropic.ToolResultBlockParam[] = [];
      for (const use of toolUses) {
        try {
          const result = await mcp.callTool({
            name: use.name,
            arguments: use.input as Record<string, unknown>,
          });
          const text = (result.content as Array<{ type: string; text?: string }>)
            .filter((c) => c.type === "text")
            .map((c) => c.text ?? "")
            .join("\n");
          results.push({
            type: "tool_result",
            tool_use_id: use.id,
            content: text || "(empty result)",
            is_error: result.isError === true,
          });
        } catch (err) {
          results.push({
            type: "tool_result",
            tool_use_id: use.id,
            content: `Tool error: ${err instanceof Error ? err.message : String(err)}`,
            is_error: true,
          });
        }
      }
      messages.push({ role: "user", content: results });

      response = await anthropic.messages.create({
        model,
        max_tokens: 8192,
        system: SYSTEM_PROMPT,
        tools,
        messages,
      });
    }

    const answer = response.content
      .filter((b): b is Anthropic.TextBlock => b.type === "text")
      .map((b) => b.text)
      .join("\n\n");

    return await renderBlocks(answer);
  } finally {
    await mcp.close().catch(() => {});
  }
}

/** Rewrite [speech:ID] markers into numbered footnotes + citation cards. */
async function renderBlocks(answer: string): Promise<AskResponse> {
  const idOrder: number[] = [];
  const markerFor = new Map<number, number>();

  const prose = answer.replace(/\[speech:(\d+)\]/g, (_, idStr: string) => {
    const id = Number(idStr);
    if (!markerFor.has(id)) {
      idOrder.push(id);
      markerFor.set(id, idOrder.length);
    }
    return `[${markerFor.get(id)}]`;
  });

  const meta = await getCitationData(idOrder);
  const metaById = new Map(meta.map((m) => [m.speechId, m]));

  const blocks: AskBlock[] = [{ type: "prose", text: prose }];
  for (const id of idOrder) {
    const m = metaById.get(id);
    if (!m) continue; // model cited an id that doesn't exist — drop the card
    blocks.push({
      type: "citation",
      marker: markerFor.get(id)!,
      speechId: m.speechId,
      speaker: m.speaker,
      meetingId: m.meetingId,
      meetingDate: m.meetingDate,
      agendaTitle: m.agendaTitle,
      quote: m.text.length > 280 ? m.text.slice(0, 277) + "…" : m.text,
      startPos: m.startPos,
      tvUrl: m.tvUrl,
    });
  }
  return { mode: "live", blocks };
}

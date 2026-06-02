import Anthropic from "@anthropic-ai/sdk";

const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

export interface LlmResponse {
  content: string;
  input_tokens: number;
  output_tokens: number;
}

export async function extractStructured<T>(params: {
  systemPrompt: string;
  userContent: string;
  schema: Record<string, unknown>;
  schemaName: string;
}): Promise<T> {
  const response = await client.messages.create({
    model: "claude-opus-4-5",
    max_tokens: 4096,
    system: params.systemPrompt,
    tools: [
      {
        name: params.schemaName,
        description: `Extract and return structured data matching the ${params.schemaName} schema`,
        input_schema: params.schema as Anthropic.Tool["input_schema"],
      },
    ],
    tool_choice: { type: "any" },
    messages: [{ role: "user", content: params.userContent }],
  });

  const toolUse = response.content.find((b) => b.type === "tool_use");
  if (!toolUse || toolUse.type !== "tool_use") {
    throw new Error(`LLM did not return tool use for ${params.schemaName}`);
  }
  return toolUse.input as T;
}

export async function chat(params: {
  system: string;
  messages: Anthropic.MessageParam[];
  maxTokens?: number;
}): Promise<string> {
  const response = await client.messages.create({
    model: "claude-opus-4-5",
    max_tokens: params.maxTokens ?? 2048,
    system: params.system,
    messages: params.messages,
  });
  const block = response.content.find((b) => b.type === "text");
  if (!block || block.type !== "text") throw new Error("No text response from LLM");
  return block.text;
}

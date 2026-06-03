"use client";

import { useRef, useState } from "react";
import { Bot, MessageSquare, Send, User } from "lucide-react";

import { ApiError, postQuery, type QueryResponse } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { formatPct, humanize } from "@/lib/format";

/**
 * One turn in the chat transcript. A user turn is just text; an assistant
 * turn carries the full governed `/query` response so we can render the
 * answer plus its citations (the agent's reasoning trace) and governance
 * badges. An assistant `error` turn renders a friendly failure message.
 */
type ChatMessage =
  | { role: "user"; text: string }
  | { role: "assistant"; response: QueryResponse }
  | { role: "assistant"; error: string };

function isError(
  m: ChatMessage,
): m is { role: "assistant"; error: string } {
  return m.role === "assistant" && "error" in m;
}

/**
 * Omnipresent "Ask LoanWhiz" chat slide-over.
 *
 * Rendered once from the app shell (see app/layout.tsx) so it is reachable
 * from every route via a floating trigger button. Plain `useState` for the
 * transcript — no state library, no streaming. On send it awaits the typed
 * `postQuery` wrapper and appends the answer (the endpoint is request/reply,
 * not a stream; it can take several seconds while the agent runs live).
 */
export function ChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  function scrollToBottom() {
    // Defer to after the DOM paints the new message.
    requestAnimationFrame(() => {
      const el = scrollRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    });
  }

  async function handleSend() {
    const question = input.trim();
    if (!question || loading) return;

    setMessages((prev) => [...prev, { role: "user", text: question }]);
    setInput("");
    setLoading(true);
    scrollToBottom();

    try {
      const response = await postQuery({ question });
      setMessages((prev) => [...prev, { role: "assistant", response }]);
    } catch (e) {
      const error =
        e instanceof ApiError
          ? e.message
          : "Something went wrong reaching LoanWhiz. Please try again.";
      setMessages((prev) => [...prev, { role: "assistant", error }]);
    } finally {
      setLoading(false);
      scrollToBottom();
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  }

  return (
    <Sheet>
      <SheetTrigger
        render={
          <Button
            size="sm"
            className="fixed bottom-6 right-6 z-50 gap-2 shadow-lg"
            aria-label="Ask LoanWhiz"
          />
        }
      >
        <MessageSquare className="size-4" />
        Ask LoanWhiz
      </SheetTrigger>
      <SheetContent
        side="right"
        className="flex w-full flex-col gap-0 p-0 sm:max-w-md"
      >
        <SheetHeader className="border-b">
          <SheetTitle className="flex items-center gap-2">
            <Bot className="size-4" />
            Ask LoanWhiz
          </SheetTitle>
          <SheetDescription>
            Ask the structured-finance agent about the deal. Answers cite the
            agent&apos;s reasoning trace.
          </SheetDescription>
        </SheetHeader>

        {/* Transcript */}
        <div
          ref={scrollRef}
          className="flex-1 space-y-4 overflow-y-auto px-4 py-4"
        >
          {messages.length === 0 && !loading ? (
            <EmptyHint />
          ) : (
            messages.map((m, i) => <MessageBubble key={i} message={m} />)
          )}
          {loading ? <ThinkingBubble /> : null}
        </div>

        {/* Composer */}
        <div className="border-t p-4">
          <div className="flex items-center gap-2">
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Ask about the deal…"
              disabled={loading}
              aria-label="Your question"
            />
            <Button
              size="icon"
              onClick={() => void handleSend()}
              disabled={loading || input.trim().length === 0}
              aria-label="Send"
            >
              <Send className="size-4" />
            </Button>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            The agent runs live — answers can take several seconds.
          </p>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function EmptyHint() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-sm text-muted-foreground">
      <Bot className="size-8 opacity-40" />
      <p>Ask a question to get started.</p>
      <p className="text-xs">
        e.g. &ldquo;Are any covenants close to breaching?&rdquo;
      </p>
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="flex max-w-[85%] items-start gap-2">
          <div className="rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
            {message.text}
          </div>
          <div className="mt-1 shrink-0 rounded-full bg-muted p-1.5">
            <User className="size-3.5" />
          </div>
        </div>
      </div>
    );
  }

  if (isError(message)) {
    return (
      <AssistantShell>
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          {message.error}
        </div>
      </AssistantShell>
    );
  }

  return (
    <AssistantShell>
      <AnswerCard response={message.response} />
    </AssistantShell>
  );
}

function AssistantShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex justify-start">
      <div className="flex max-w-[90%] items-start gap-2">
        <div className="mt-1 shrink-0 rounded-full bg-muted p-1.5">
          <Bot className="size-3.5" />
        </div>
        <div className="min-w-0 space-y-2">{children}</div>
      </div>
    </div>
  );
}

function AnswerCard({ response }: { response: QueryResponse }) {
  const { answer, reasoning_trace, overall_status, aggregate_confidence } =
    response;
  return (
    <>
      <div className="rounded-lg bg-muted px-3 py-2 text-sm whitespace-pre-wrap">
        {answer}
      </div>

      {/* Governance badges */}
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant="secondary" className="font-normal">
          {humanize(overall_status)}
        </Badge>
        <Badge variant="outline" className="font-normal">
          {formatPct(aggregate_confidence * 100)} confidence
        </Badge>
        {response.human_review_required ? (
          <Badge variant="destructive" className="font-normal">
            Human review required
          </Badge>
        ) : null}
      </div>

      {/* Citations — the agent's reasoning trace */}
      {reasoning_trace.length > 0 ? (
        <div className="rounded-lg border bg-background px-3 py-2">
          <p className="mb-1 text-xs font-medium text-muted-foreground">
            Sources
          </p>
          <ol className="list-decimal space-y-1 pl-4 text-xs text-muted-foreground">
            {reasoning_trace.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        </div>
      ) : null}
    </>
  );
}

function ThinkingBubble() {
  return (
    <AssistantShell>
      <div className="flex items-center gap-2 rounded-lg bg-muted px-3 py-2 text-sm text-muted-foreground">
        <span className="size-2 animate-pulse rounded-full bg-muted-foreground/60" />
        Thinking…
      </div>
    </AssistantShell>
  );
}

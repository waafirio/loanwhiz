import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/**
 * The illustrative authentication sketch on the MCP page.
 *
 * **This draws a flow that does not exist.** The MCP server has no
 * authentication of any kind — no token, no bearer, no API key — and this
 * component adds none. It is a picture of where authentication is *going*,
 * shown because the destination is worth stating; every element of it is
 * marked so that a screenshot cropped out of context still reads as a sketch
 * rather than as a working setup screen.
 *
 * Rendering an unauthenticated server as an authenticated one would be a false
 * claim about a security property — the one lie this platform cannot afford,
 * and the exact inverse of the discipline it applies to data everywhere else
 * (a synthetic tape says so, a refusal names its reason, a tally is read live
 * rather than transcribed).
 *
 * **Inert by construction.** No `fetch`, no `onSubmit`, no `action=`, no
 * state, no input elements: there is nothing here for a viewer to submit and
 * nothing for a later edit to accidentally wire up. It is a plain server
 * component — it cannot even hold a keystroke.
 *
 * `tests/test_mcp_page.py` holds every one of those properties as an
 * assertion. That is deliberate: #484 committed synthetic pools correctly
 * labelled in the data, and the Pool and Waterfall pages still render no
 * synthetic badge — a marking that lives only in an author's intention does
 * not survive. #571 hit the same boundary from the other side, its provenance
 * guard walking `src/loanwhiz/**` Python only, so a `web/` qualifier was
 * guaranteed to *arrive* present and never to be *displayed*.
 */

/** The marking. Removing it reds `tests/test_mcp_page.py`. */
export const SKETCH_MARKING =
  "NOT IMPLEMENTED — illustration only; this server has no authentication";

/** Where authentication will actually live, named so the sketch is not a promise. */
export const AUTH_OWNER_SENTENCE =
  "Authentication will live in waafir-platform, which LoanWhiz is intended to run on top of and which owns that concern. Nothing on this page authenticates anything today, and the MCP server accepts every caller.";

/**
 * The steps a future setup flow would have. Each renders with its own marking
 * badge from the single map below, so a step cannot be added without one.
 */
const SKETCH_STEPS: { title: string; body: string }[] = [
  {
    title: "Register the client",
    body: "A future operator would register an MCP client against their waafir-platform tenant, and the platform would issue the credential. No registry exists; nothing is issued.",
  },
  {
    title: "Present a credential",
    body: "The client would present that credential on connect, and the platform would resolve it to a tenant before any tool is dispatched. Today the server dispatches every call it receives, unauthenticated.",
  },
  {
    title: "Scope the tool surface",
    body: "The resolved tenant would narrow the callable tool list to what it is entitled to. Today `is_exposed_as_tool()` is the whole of the exposure decision, and it asks nothing about who is calling.",
  },
];

export function McpAuthSketch() {
  return (
    <Card className="border-2 border-dashed border-destructive/60 bg-destructive/[0.03]">
      <CardHeader className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="destructive" className="font-mono text-xs uppercase">
            {SKETCH_MARKING}
          </Badge>
        </div>
        <CardTitle className="text-base">
          Authentication — a sketch of a planned flow, not a working one
        </CardTitle>
        <p className="text-sm text-muted-foreground">{AUTH_OWNER_SENTENCE}</p>
        <p className="text-sm font-medium text-destructive">
          Everything in this section is a drawing. It is shown to say where
          authentication is going, not to suggest that any of it runs. Do not
          read this section as evidence that the MCP server is secured — it is
          not, by a deliberate decision to let waafir-platform own that.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        {SKETCH_STEPS.map((step, i) => (
          <div
            key={step.title}
            className="space-y-1.5 rounded-md border border-dashed border-destructive/40 p-3"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-muted-foreground">
                {i + 1}
              </span>
              <span className="text-sm font-semibold">{step.title}</span>
              <Badge
                variant="destructive"
                className="font-mono text-[10px] uppercase"
              >
                {SKETCH_MARKING}
              </Badge>
            </div>
            <p className="text-sm text-muted-foreground">{step.body}</p>
          </div>
        ))}
        <p className="border-t border-dashed border-destructive/40 pt-3 text-xs font-medium text-destructive">
          {SKETCH_MARKING}. Repeated on every element above so that a crop of
          any one of them still carries it.
        </p>
      </CardContent>
    </Card>
  );
}

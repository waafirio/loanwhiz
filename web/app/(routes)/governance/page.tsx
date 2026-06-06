"use client";

import { useState } from "react";
import { Scale, Send, ShieldCheck } from "lucide-react";

import {
  ApiError,
  getGovernance,
  postQuery,
  type GovernanceEvidencePack,
} from "@/lib/api";
import { PackBody, PackSkeleton } from "@/components/evidence-pack-sheet";
import { PageHeader } from "@/components/page-states";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";

/**
 * Governance view (#239) — the FINOS evidence-pack / audit-trail / model-risk
 * surface elevated to a top-level route, the challenge's trust differentiator.
 *
 * The user asks the agent a question; the page runs the governed `/query`,
 * loads the resulting `GovernanceEvidencePack`, and renders it inline (reusing
 * the same `PackBody` the chat slide-over uses): the aggregate + per-tool
 * confidence, the citation trail, the tool-call audit/reasoning trace,
 * `finos_compliant`, and the **data provenance** (deeploans vs direct) of every
 * tape the answer relied on. All values are honest — the evidence pack is
 * derived server-side from the actual run, not asserted here.
 */
export default function GovernancePage() {
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [pack, setPack] = useState<GovernanceEvidencePack | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    const question = input.trim();
    if (!question || loading) return;
    setLoading(true);
    setError(null);
    try {
      const response = await postQuery({ question });
      if (!response.evidence_pack_id) {
        setPack(null);
        setError("The agent answered but produced no governance evidence pack.");
        return;
      }
      const loaded = await getGovernance(response.evidence_pack_id);
      setPack(loaded);
    } catch (e) {
      setPack(null);
      setError(
        e instanceof ApiError
          ? e.message
          : "Failed to run the governed query. Is the API reachable?",
      );
    } finally {
      setLoading(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void run();
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Governance"
        description="The auditable trail behind every agent answer — confidence, citations, the tool-call audit log, FINOS compliance, and data provenance (deeploans vs direct)."
      />

      {/* What this surface is — the FINOS trust story, stated plainly. */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Scale className="size-4" />
            FINOS-aligned governance
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <p>
            Every governed query emits a single evidence pack implementing the
            FINOS AI Governance Framework: an append-only audit trail of which
            tools were called, a conservative aggregate confidence
            (<code>min</code> of the per-tool scores), the deduplicated citation
            trail, a human-review flag below the 0.70 threshold, and a real
            <code> finos_compliant</code> consistency check (not a hardcoded
            value).
          </p>
          <p>
            Each tape citation also records its{" "}
            <span className="font-medium text-foreground">data provenance</span>{" "}
            — whether the tape was ingested through the{" "}
            <span className="font-medium text-foreground">deeploans</span> ETL
            backend (Algoritmica&apos;s open-source ESMA tool) or read directly
            from its source URL — so the trust story extends all the way down to
            where the data came from.
          </p>
        </CardContent>
      </Card>

      {/* Query composer — runs the live governed query. */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Run a governed query</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="flex items-center gap-2">
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="e.g. Are any covenants close to breaching?"
              disabled={loading}
              aria-label="Governed query"
            />
            <Button
              onClick={() => void run()}
              disabled={loading || input.trim().length === 0}
              className="gap-1.5"
            >
              <Send className="size-4" />
              Run
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            The agent runs live — answers can take several seconds.
          </p>
        </CardContent>
      </Card>

      {/* Evidence pack — the auditable trail. */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <ShieldCheck className="size-4" />
            Governance evidence
          </CardTitle>
        </CardHeader>
        <CardContent>
          {error ? (
            <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
              {error}
            </div>
          ) : loading ? (
            <PackSkeleton />
          ) : pack ? (
            <PackBody pack={pack} />
          ) : (
            <p className="text-sm text-muted-foreground">
              Run a query above to see the full governance evidence pack behind
              the answer — confidence, citations, the audit trail, FINOS
              compliance, and data provenance.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

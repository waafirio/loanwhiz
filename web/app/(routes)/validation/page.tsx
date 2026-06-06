"use client";

import { useEffect, useState } from "react";

import {
  ApiError,
  getValidation,
  type ValidationReport,
  type ValidationPeriod,
  type ValidationWaterfall,
  type ValidationStep,
  type ValidationSource,
} from "@/lib/api";
import { useSelectedDeal } from "@/lib/deal-context";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "@/components/page-states";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { humanize } from "@/lib/format";

/**
 * Validation view — epic #206's headline seasoned-deal proof, surfaced in the
 * product.
 *
 * Renders the engine-validation report from `GET /deal/{id}/validation`: our
 * model-driven waterfall engine reconciled, **to the cent**, against the deal's
 * own *published* Notes & Cash Priority of Payments. The per-step table carries
 * the honest `source` label — engine-COMPUTED lines (the independent proof) vs.
 * report-supplied lines (the engine only routes them) vs. a residual sweep — so
 * nothing is overclaimed as a blanket 100%.
 *
 * A deal with no committed validation fixture (e.g. the seasoned 2023-1) returns
 * `available: false`; the page then shows an honest "no published proof" state.
 */
export default function ValidationPage() {
  const { dealId } = useSelectedDeal();
  // Tag the result with its deal so a deal switch falls back to the loading
  // state without a synchronous setState in the effect (see Overview page).
  const [state, setState] = useState<{
    dealId: string;
    data: ValidationReport | null;
    error: string | null;
  }>({ dealId, data: null, error: null });

  useEffect(() => {
    let cancelled = false;
    getValidation(dealId)
      .then((d) => !cancelled && setState({ dealId, data: d, error: null }))
      .catch(
        (e) =>
          !cancelled &&
          setState({
            dealId,
            data: null,
            error:
              e instanceof ApiError
                ? e.message
                : "Failed to load validation report",
          }),
      );
    return () => {
      cancelled = true;
    };
  }, [dealId]);

  const current = state.dealId === dealId ? state : null;
  const data = current?.data ?? null;
  const error = current?.error ?? null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Validation"
        description="Our waterfall engine reconciled against the deal's own published Notes & Cash Priority of Payments — to the cent."
      />
      {error ? (
        <ErrorState title="Could not load validation report" message={error} />
      ) : !data ? (
        <LoadingState />
      ) : !data.available ? (
        <EmptyState
          message={
            data.note ??
            "No published validation proof for this deal. The engine-vs-published reconciliation requires a published Notes & Cash report fixture this deal does not have."
          }
        />
      ) : (
        <ValidationContent report={data} />
      )}
    </div>
  );
}

/** EUR with cents — the proof is "to the cent", so whole-EUR rounding won't do. */
function formatEurCents(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("en-IE", {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function ValidationContent({ report }: { report: ValidationReport }) {
  // Headline counts across all periods (revenue/redemption steps reconciled).
  const totals = report.periods.reduce(
    (acc, p) => ({
      revenueSteps: acc.revenueSteps + p.revenue.steps.length,
      revenuePassed: acc.revenuePassed + p.revenue.steps_passed,
      redemptionSteps: acc.redemptionSteps + p.redemption.steps.length,
      redemptionPassed: acc.redemptionPassed + p.redemption.steps_passed,
    }),
    { revenueSteps: 0, revenuePassed: 0, redemptionSteps: 0, redemptionPassed: 0 },
  );

  return (
    <div className="space-y-6">
      {/* Headline claim — stated precisely, not as a blanket 100%. */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            {report.passed ? "Engine reproduces the published PoP" : "Reconciliation result"}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm leading-relaxed">
            Our waterfall engine reproduces this deal&apos;s actual published
            Notes &amp; Cash Priority of Payments — revenue{" "}
            <span className="font-semibold tabular-nums">
              {totals.revenuePassed}/{totals.revenueSteps}
            </span>
            , redemption{" "}
            <span className="font-semibold tabular-nums">
              {totals.redemptionPassed}/{totals.redemptionSteps}
            </span>
            , to the cent (tolerance €{report.tolerance_eur.toFixed(2)}).
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Badge variant={report.passed ? "default" : "destructive"}>
              {report.passed ? "PASS" : "FAIL"}
            </Badge>
            <span className="text-sm text-muted-foreground">
              {report.periods_passed}/{report.periods_checked} reporting period
              {report.periods_checked === 1 ? "" : "s"} reconciled
            </span>
          </div>
        </CardContent>
      </Card>

      {report.periods.map((period) => (
        <PeriodCard key={`${period.reporting_date}-${period.period_label}`} period={period} />
      ))}

      {/* Honesty disclosure — which lines were engine-computed vs report-supplied. */}
      {report.source_note ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">What the engine derives vs. what the report supplies</CardTitle>
          </CardHeader>
          <CardContent className="text-sm leading-relaxed text-muted-foreground">
            {report.source_note}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

function PeriodCard({ period }: { period: ValidationPeriod }) {
  return (
    <div className="space-y-4">
      <h2 className="text-lg font-medium">
        {period.period_label}
        <span className="ml-2 text-sm font-normal text-muted-foreground">
          ({period.reporting_date})
        </span>
      </h2>
      <WaterfallCard
        title="Revenue Priority of Payments"
        waterfall={period.revenue}
      />
      <WaterfallCard
        title="Redemption Priority of Payments"
        waterfall={period.redemption}
      />
    </div>
  );
}

function WaterfallCard({
  title,
  waterfall,
}: {
  title: string;
  waterfall: ValidationWaterfall;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between text-base">
          <span>{title}</span>
          <span className="text-sm font-normal text-muted-foreground tabular-nums">
            {waterfall.steps_passed}/{waterfall.steps.length} steps
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-12">Step</TableHead>
              <TableHead>Recipient</TableHead>
              <TableHead>Source</TableHead>
              <TableHead className="text-right">Engine</TableHead>
              <TableHead className="text-right">Published</TableHead>
              <TableHead className="text-right">Δ</TableHead>
              <TableHead className="text-right">Match</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {waterfall.steps.map((step) => (
              <StepRow key={`${step.priority}-${step.recipient}`} step={step} />
            ))}
          </TableBody>
        </Table>

        {/* The report's own documented undistributed rounding remainder. */}
        {waterfall.unapplied_rounding > 0 ? (
          <p className="text-xs text-muted-foreground">
            Unapplied due to rounding (published, left undistributed):{" "}
            <span className="tabular-nums">
              {formatEurCents(waterfall.unapplied_rounding)}
            </span>
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

/** Visual variant per honesty source — engine-computed reads as the proof. */
function sourceBadge(source: ValidationSource): {
  label: string;
  variant: "default" | "secondary" | "outline";
} {
  switch (source) {
    case "engine":
      return { label: "engine", variant: "default" };
    case "report-supplied":
      return { label: "report-supplied", variant: "secondary" };
    default:
      return { label: source, variant: "outline" };
  }
}

function StepRow({ step }: { step: ValidationStep }) {
  const badge = sourceBadge(step.source);
  return (
    <TableRow>
      <TableCell className="font-medium tabular-nums">{step.priority}</TableCell>
      <TableCell>{humanize(step.recipient)}</TableCell>
      <TableCell>
        <Badge variant={badge.variant}>{badge.label}</Badge>
      </TableCell>
      <TableCell className="text-right tabular-nums">
        {formatEurCents(step.engine_amount)}
      </TableCell>
      <TableCell className="text-right tabular-nums">
        {formatEurCents(step.report_amount)}
      </TableCell>
      <TableCell className="text-right tabular-nums">
        {formatEurCents(step.delta)}
      </TableCell>
      <TableCell className="text-right">
        {step.passed ? (
          <span className="text-emerald-600">✓</span>
        ) : (
          <span className="text-destructive">✗</span>
        )}
      </TableCell>
    </TableRow>
  );
}

"use client";

import { useEffect, useState } from "react";

import {
  ApiError,
  getBook,
  type BookResponse,
  type PositionField,
} from "@/lib/api";
import {
  BookDisclosure,
  PositionFactCell,
  PositionProvenanceBadge,
} from "@/components/evidence-pack-sheet";
import { ErrorState, LoadingState, PageHeader } from "@/components/page-states";
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
import { formatCurrency, humanize } from "@/lib/format";

/**
 * The holder's book — `GET /book` (#573, epic #569).
 *
 * The screen #571's position model and #572's endpoint were built for. Two
 * things about it are the point rather than decoration on it:
 *
 * 1. Every row says what the position IS. #484 shipped synthetic pools that
 *    are correctly labelled in the data and render on the Pool and Waterfall
 *    pages with no badge at all, so a viewer sees generated collateral
 *    presented exactly like real collateral. An illustrative position that
 *    renders identically to a real one has defeated the qualifier entirely.
 * 2. A fact the platform could not resolve renders at the same weight as one
 *    it could (#549) — never as an empty cell, which a reader takes for zero.
 *    On the committed book this is the common case, not the edge case: every
 *    position's coupon refuses, so a blank column would read as a book of
 *    zero-coupon paper.
 *
 * The provenance and refusal rendering lives in `evidence-pack-sheet.tsx`,
 * beside the provenance vocabulary this platform already had.
 */

/**
 * The fields the book reports, in the order the API returns them.
 *
 * Collected by walking every position's facts rather than reading the first
 * position's — a field is never dropped from a position's `facts` (#572), and
 * a header derived from one row would silently narrow the table if that ever
 * stopped being true. First-seen order preserves the API's own render order.
 */
function bookFields(book: BookResponse): string[] {
  const seen: string[] = [];
  for (const position of book.positions) {
    for (const fact of position.facts) {
      if (!seen.includes(fact.field)) seen.push(fact.field);
    }
  }
  return seen;
}

export default function BookPage() {
  const [book, setBook] = useState<BookResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getBook()
      .then(setBook)
      .catch((e) =>
        setError(
          e instanceof ApiError ? e.message : "Failed to load the book.",
        ),
      );
  }, []);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Book"
        description="A holder's positions, each joined to the facts the platform can resolve — and marked with what it could not."
      />
      {error ? (
        <ErrorState title="Could not load the book" message={error} />
      ) : !book ? (
        <LoadingState />
      ) : (
        <BookContent book={book} />
      )}
    </div>
  );
}

function BookContent({ book }: { book: BookResponse }) {
  const fields = bookFields(book);
  return (
    <div className="space-y-4">
      {/*
        The book's status renders above the positions, not below them. A size
        read before its qualifier has already been read as an exposure; the
        qualifier arriving afterwards corrects a reader who is still looking.
      */}
      <BookDisclosure book={book} />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{book.name}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Position</TableHead>
                  <TableHead className="text-right">Size held</TableHead>
                  {fields.map((field) => (
                    <TableHead key={field}>{humanize(field)}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {book.positions.map((position, i) => (
                  <TableRow
                    // Nothing guarantees (deal_id, tranche) unique: `Book.positions`
                    // is a plain tuple, so a holder may hold one class twice. The
                    // index disambiguates rather than assuming the data cannot.
                    key={`${position.deal_id}::${position.tranche}::${i}`}
                    className="align-top"
                  >
                    <TableCell>
                      <div className="space-y-1">
                        <p className="text-sm font-medium">
                          {position.deal_name}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {humanize(position.tranche)} ·{" "}
                          {position.strips.join(", ")} · as at {position.as_of}
                        </p>
                        {/*
                          Per row, never once per screen: a book-level caveat
                          says nothing about the row a reader is looking at.
                        */}
                        <PositionProvenanceBadge
                          provenance={position.provenance}
                          describesARealHolding={
                            position.describes_a_real_holding
                          }
                        />
                      </div>
                    </TableCell>
                    <TableCell className="text-right text-sm tabular-nums">
                      {formatCurrency(position.size)}
                    </TableCell>
                    {/*
                      Mapped, never looked up by name. A `.find()` that missed
                      would render an empty cell, and an empty cell and a
                      refused one must not read alike (#572/#494).
                    */}
                    {position.facts.map((fact: PositionField) => (
                      <TableCell key={fact.field} className="max-w-56">
                        <PositionFactCell fact={fact} />
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

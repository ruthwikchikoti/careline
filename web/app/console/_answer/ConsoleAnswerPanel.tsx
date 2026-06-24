"use client";

import type { AnswerResult } from "@/lib/api";
import { GroundedAnswer } from "@/components/answer/GroundedAnswer";
import { useDemoFacts } from "./use-demo-facts";

// Console-wired grounded answer: pulls the demo patient's valid slice so the
// GroundedAnswer panel can resolve each citation to its approved fact summary.
export function ConsoleAnswerPanel({ result }: { result: AnswerResult }) {
  const facts = useDemoFacts();
  return <GroundedAnswer result={result} facts={facts} />;
}

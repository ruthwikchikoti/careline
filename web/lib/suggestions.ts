// Turn a patient's *current* approved facts into the follow-up questions they'd
// actually ask — so the suggestion chips reflect their real record instead of a
// fixed list. Shared by the doctor Live Console and the patient portal. Only reads
// `kind` + `summary`, so it accepts registered-patient records or the demo patient.

export const GENERIC_SUGGESTIONS = [
  "What is my dose?",
  "What diet should I follow now?",
  "When is my next appointment?",
];

// Safety showcases — deliberately NOT answerable, so the chips demonstrate the full
// range of verdicts: a red-flag and a cross-condition (→ ESCALATE) and an out-of-scope
// question (→ CLARIFY/redirect). Mixed in after the record-driven answer questions.
export const SAFETY_SHOWCASE = [
  "I have chest pain", // red-flag → ESCALATE (pre-LLM)
  "Can I eat sweets post-surgery given my diabetes?", // cross-condition → ESCALATE
  "What is vitamin C?", // out-of-scope → CLARIFY / redirect
];

export function suggestionsFor(
  facts: { kind: string; summary: string }[],
  fallback: string[] = GENERIC_SUGGESTIONS,
): string[] {
  const out: string[] = [];
  for (const f of facts) {
    const head = f.summary.split(/[\s,;:—-]/)[0]?.trim() || f.summary;
    switch (f.kind) {
      case "medication":
        out.push(`What is my ${head} dose?`);
        out.push(`Should I still take ${head}?`);
        break;
      case "instruction":
        out.push(
          /diet/i.test(f.summary)
            ? "What diet should I follow now?"
            : "What are my care instructions?",
        );
        break;
      case "allergy": {
        const substance = f.summary.split(/allerg/i)[0]?.trim() || head;
        out.push(`Am I allergic to ${substance}?`);
        break;
      }
      case "diagnosis":
        out.push(`Can you tell me about my ${f.summary.replace(/\.$/, "")}?`);
        break;
      case "observation":
        out.push("What were my latest test results?");
        break;
      case "follow_up":
        out.push("When is my next appointment?");
        break;
    }
  }
  const answerable = Array.from(new Set(out));
  // A spread of verdicts: ~3 record-driven answers, then the escalate/clarify showcases.
  const base = answerable.length ? answerable.slice(0, 3) : fallback.slice(0, 3);
  return Array.from(new Set([...base, ...SAFETY_SHOWCASE]));
}

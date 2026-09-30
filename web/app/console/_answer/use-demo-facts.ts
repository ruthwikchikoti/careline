"use client";

import { useEffect, useState } from "react";
import { getDemoPatient, type DemoFact } from "@/lib/api";

// The console asks many questions per call; resolving citations to fact summaries
// needs the patient's valid slice. Fetch it once and memoise at module scope so each
// answer panel reuses the same facts instead of refetching per turn.
let cache: DemoFact[] | null = null;
let inflight: Promise<DemoFact[]> | null = null;

function loadFacts(): Promise<DemoFact[]> {
  if (cache) return Promise.resolve(cache);
  if (!inflight) {
    inflight = getDemoPatient()
      .then((p) => {
        cache = p.current_facts;
        return cache;
      })
      .catch(() => {
        inflight = null; // allow a later retry
        return [];
      });
  }
  return inflight;
}

export function useDemoFacts(): DemoFact[] {
  const [facts, setFacts] = useState<DemoFact[]>(cache ?? []);
  useEffect(() => {
    let active = true;
    loadFacts().then((f) => {
      if (active) setFacts(f);
    });
    return () => {
      active = false;
    };
  }, []);
  return facts;
}

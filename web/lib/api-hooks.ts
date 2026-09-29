"use client";

import { useQuery } from "@tanstack/react-query";
import { getConsultation, listConsultations } from "@/lib/api";

export function useConsultation(id: string) {
  return useQuery({
    queryKey: ["consultation", id],
    queryFn: () => getConsultation(id),
    enabled: Boolean(id),
  });
}

export function useConsultations() {
  return useQuery({
    queryKey: ["consultations"],
    queryFn: listConsultations,
  });
}

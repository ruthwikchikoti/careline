/** Tiny classnames joiner (avoids a clsx dependency for the foundation). */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

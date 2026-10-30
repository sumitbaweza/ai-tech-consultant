import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** Join class names, letting a later Tailwind class win over an earlier one ("p-2 p-4" -> "p-4"). */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

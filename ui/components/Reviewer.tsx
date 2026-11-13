"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

const STORAGE_KEY = "einvoice.reviewer";

type Reviewer = { name: string; setName: (name: string) => void; problem: string | null };

const ReviewerContext = createContext<Reviewer>({ name: "", setName: () => {}, problem: null });

/** Why this name cannot be recorded, or null if it can. */
function problemWith(name: string): string | null {
  if (name.trim() === "") return "Enter your name to save corrections or approve.";
  if (name.length > 100) return "The name is too long (100 characters at most).";
  // The name travels in an HTTP header, which only carries Latin-1.
  if ([...name].some((char) => char.charCodeAt(0) > 255)) {
    return "Use Latin letters only; this name cannot be sent to the API.";
  }
  return null;
}

export function ReviewerProvider({ children }: { children: ReactNode }) {
  const [name, setNameState] = useState("");

  useEffect(() => {
    try {
      setNameState(window.localStorage.getItem(STORAGE_KEY) ?? "");
    } catch {
      // Storage blocked: the name simply is not remembered between visits.
    }
  }, []);

  const setName = (next: string) => {
    setNameState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // See above.
    }
  };

  return (
    <ReviewerContext.Provider value={{ name, setName, problem: problemWith(name) }}>
      {children}
    </ReviewerContext.Provider>
  );
}

export const useReviewer = () => useContext(ReviewerContext);

export function ReviewerField() {
  const { name, setName } = useReviewer();
  return (
    <label className="reviewer">
      <span>Reviewing as</span>
      <input
        value={name}
        onChange={(event) => setName(event.target.value)}
        placeholder="Your name"
        autoComplete="name"
        maxLength={100}
      />
    </label>
  );
}

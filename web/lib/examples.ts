// Suggested questions shown under the ask box. The example repos get questions
// that were verified against them in the Phase 1-2 acceptance runs, including
// one each that the code can't answer, to show the "not found" behavior.

const GENERIC = ["What is the main entry point?", "How are errors handled?", "How is configuration loaded?"];

const BY_REPO: Record<string, string[]> = {
  "pallets/click": [
    "What is the main entry point when a click command is invoked?",
    "What does the make_context method do?",
    "How does click connect to a PostgreSQL database?",
  ],
  "expressjs/express": [
    "Where is the application object created when you call express()?",
    "How does app.listen start the HTTP server?",
    "Where does Express implement WebSocket support?",
  ],
  "sindresorhus/ky": [
    "How does ky decide whether to retry a failed request and how long to wait?",
    "Where is the GraphQL query builder implemented?",
  ],
};

export function suggestedQuestions(repoId: string): string[] {
  return BY_REPO[repoId.toLowerCase()] ?? GENERIC;
}

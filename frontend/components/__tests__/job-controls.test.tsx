import { readFileSync } from "node:fs";
import { describe, it, expect } from "vitest";

const source = readFileSync(new URL("../job-controls.tsx", import.meta.url), "utf8");

describe("JobControls source contract", () => {
  it("starts compact and delegates failed-stage retry to the task failure banner", () => {
    expect(source).toContain("const [minimized, setMinimized] = useState(true)");
    expect(source).toContain("<SlidersHorizontal size={16} /> 任務操作");
    expect(source).not.toContain("/retry-stage");
    expect(source).not.toContain("重試失敗階段");
  });
});

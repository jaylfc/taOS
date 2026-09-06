import { useState } from "react";
import { evaluate } from "mathjs";
import { Button } from "@/components/ui";

const buttons = [
  ["C", "±", "%", "÷"],
  ["7", "8", "9", "×"],
  ["4", "5", "6", "−"],
  ["1", "2", "3", "+"],
  ["0", ".", "⌫", "="],
];

function toMathExpr(display: string): string {
  return display.replace(/÷/g, "/").replace(/×/g, "*").replace(/−/g, "-");
}

export function CalculatorApp({ windowId: _windowId }: { windowId: string }) {
  const [expression, setExpression] = useState("0");
  const [result, setResult] = useState("");
  const [hasResult, setHasResult] = useState(false);

  function handleButton(label: string) {
    switch (label) {
      case "C":
        setExpression("0");
        setResult("");
        setHasResult(false);
        break;

      case "±": {
        if (hasResult && result) {
          const negated = result.startsWith("-") ? result.slice(1) : `-${result}`;
          setExpression(negated);
          setResult("");
          setHasResult(false);
        } else if (expression !== "0") {
          setExpression((prev) =>
            prev.startsWith("-") ? prev.slice(1) : `-${prev}`
          );
        }
        break;
      }

      case "%": {
        try {
          const val = evaluate(toMathExpr(expression));
          const pct = Number(val) / 100;
          setExpression(String(pct));
          setResult("");
          setHasResult(false);
        } catch {
          // ignore invalid expressions
        }
        break;
      }

      case "⌫":
        if (hasResult) {
          setExpression("0");
          setResult("");
          setHasResult(false);
        } else {
          setExpression((prev) => (prev.length > 1 ? prev.slice(0, -1) : "0"));
        }
        break;

      case "=": {
        try {
          const val = evaluate(toMathExpr(expression));
          setResult(String(val));
          setHasResult(true);
        } catch {
          setResult("Error");
          setHasResult(true);
        }
        break;
      }

      case "÷":
      case "×":
      case "−":
      case "+": {
        if (hasResult && result && result !== "Error") {
          setExpression(result + label);
          setResult("");
          setHasResult(false);
        } else {
          setExpression((prev) => prev + label);
        }
        break;
      }

      default: {
        // digit or dot
        if (hasResult) {
          setExpression(label === "." ? "0." : label);
          setResult("");
          setHasResult(false);
        } else {
          setExpression((prev) =>
            prev === "0" && label !== "." ? label : prev + label
          );
        }
        break;
      }
    }
  }

  return (
    <div className="flex flex-col h-full bg-shell-bg-deep select-none">
      {/* Display */}
      <div className="flex flex-col items-end justify-end px-4 py-3 min-h-[100px] gap-1">
        <span
          className="text-shell-text-secondary text-lg break-all text-right leading-tight"
          aria-label="Expression"
        >
          {expression}
        </span>
        <span
          className="text-shell-text text-3xl font-semibold break-all text-right leading-tight min-h-[2.25rem]"
          aria-label="Result"
        >
          {result}
        </span>
      </div>

      {/* Button grid */}
      <div className="grid grid-cols-4 gap-1 flex-1 p-1">
        {buttons.flat().map((label) => {
          const isOperator = ["÷", "×", "−", "+"].includes(label);
          const isEquals = label === "=";
          const isTopRow = ["C", "±", "%", "⌫"].includes(label);

          const variant: "default" | "outline" | "secondary" | "ghost" =
            isEquals
              ? "default"
              : isOperator
                ? "outline"
                : isTopRow
                  ? "ghost"
                  : "secondary";

          return (
            <Button
              key={label}
              variant={variant}
              className="h-full text-xl font-medium rounded-lg active:scale-95"
              onClick={() => handleButton(label)}
              aria-label={label}
            >
              {label}
            </Button>
          );
        })}
      </div>
    </div>
  );
}

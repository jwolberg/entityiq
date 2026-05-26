import { render, screen } from "@testing-library/react";
import App from "./App";

describe("App", () => {
  it("renders the EntityIQ heading", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: /entityiq/i })).toBeDefined();
  });

  it("renders the platform description", () => {
    render(<App />);
    expect(
      screen.getByText(/enterprise business verification/i)
    ).toBeDefined();
  });
});

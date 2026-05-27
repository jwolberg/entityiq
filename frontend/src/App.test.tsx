import { render, screen } from "@testing-library/react";
import App from "./App";

// App now renders the sign-in form before auth is established.
// Mock fetch so the sign-in API call does not reach the network.
vi.stubGlobal("fetch", vi.fn());

describe("App", () => {
  it("renders the EntityIQ sign-in form before auth", () => {
    render(<App />);
    // The sign-in form contains an email input and submit button
    expect(screen.getByTestId("email-input")).toBeDefined();
    expect(screen.getByTestId("sign-in-button")).toBeDefined();
  });

  it("shows the platform name in the sign-in card", () => {
    render(<App />);
    // The sign-in card has an EntityIQ heading
    expect(screen.getByRole("heading", { name: /entityiq/i })).toBeDefined();
  });
});

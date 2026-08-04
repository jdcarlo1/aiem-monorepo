import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import Login from "@/pages/login";

// Mock wouter's navigate so we can observe it
const mockSetLocation = vi.fn();
vi.mock("wouter", async (importOriginal) => {
  const real = await importOriginal<typeof import("wouter")>();
  return {
    ...real,
    useLocation: () => ["/", mockSetLocation],
  };
});

vi.mock("@/lib/auth", async () => {
  const mod = await import("@/lib/auth");
  return {
    ...mod,
    setToken: vi.fn(),
    setCsrfToken: vi.fn(),
    getToken: vi.fn(() => null),
    clearToken: vi.fn(),
  };
});

function renderLogin() {
  return render(<Login />);
}

describe("Login page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    global.fetch = vi.fn();
  });

  it("renders the AIEM brand heading and institutional subtitle", () => {
    renderLogin();
    expect(screen.getByText("AIEM Terminal")).toBeInTheDocument();
    expect(screen.getByText(/INSTITUTIONAL ACCESS/i)).toBeInTheDocument();
  });

  it("shows password mode by default with username and password fields", () => {
    renderLogin();
    expect(screen.getByPlaceholderText("Enter username")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("••••••••")).toBeInTheDocument();
  });

  it("switches to token mode when the Admin Token tab is clicked", async () => {
    const user = userEvent.setup();
    renderLogin();

    await user.click(screen.getByText("Admin Token"));

    expect(screen.getByPlaceholderText("Paste token…")).toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText("Enter username")
    ).not.toBeInTheDocument();
  });

  it("submit button is disabled when token field is empty in token mode", async () => {
    const user = userEvent.setup();
    renderLogin();
    await user.click(screen.getByText("Admin Token"));

    const submit = screen.getByRole("button", {
      name: /Authenticate/i,
    });
    expect(submit).toBeDisabled();
  });

  it("stores the token and navigates to /command on token submit", async () => {
    const { setToken } = await import("@/lib/auth");
    const user = userEvent.setup();
    renderLogin();

    await user.click(screen.getByText("Admin Token"));
    await user.type(
      screen.getByPlaceholderText("Paste token…"),
      "my-secret-token"
    );
    await user.click(screen.getByRole("button", { name: /Authenticate/i }));

    expect(setToken).toHaveBeenCalledWith("my-secret-token");
    expect(mockSetLocation).toHaveBeenCalledWith("/command");
  });

  it("shows an error message when password login returns 401", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: async () => ({ error: "Invalid credentials" }),
    });

    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByPlaceholderText("Enter username"), "baduser");
    await user.type(screen.getByPlaceholderText("••••••••"), "badpass");
    await user.click(screen.getByRole("button", { name: /Sign In/i }));

    await waitFor(() => {
      expect(screen.getByText(/Invalid credentials/i)).toBeInTheDocument();
    });
  });

  it("shows a rate-limit error when the server returns 429", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 429,
      json: async () => ({}),
    });

    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByPlaceholderText("Enter username"), "user");
    await user.type(screen.getByPlaceholderText("••••••••"), "pass");
    await user.click(screen.getByRole("button", { name: /Sign In/i }));

    await waitFor(() => {
      expect(screen.getByText(/Too many attempts/i)).toBeInTheDocument();
    });
  });

  it("shows a network error when fetch rejects", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("Network error")
    );

    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByPlaceholderText("Enter username"), "user");
    await user.type(screen.getByPlaceholderText("••••••••"), "pass");
    await user.click(screen.getByRole("button", { name: /Sign In/i }));

    await waitFor(() => {
      expect(screen.getByText(/Network error/i)).toBeInTheDocument();
    });
  });

  it("submit button is disabled while a login request is in flight", async () => {
    let resolveResponse!: (v: unknown) => void;
    (global.fetch as ReturnType<typeof vi.fn>).mockReturnValueOnce(
      new Promise((res) => {
        resolveResponse = res;
      })
    );

    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByPlaceholderText("Enter username"), "user");
    await user.type(screen.getByPlaceholderText("••••••••"), "pass");

    const submit = screen.getByRole("button", { name: /Sign In/i });
    await user.click(submit);

    await waitFor(() => {
      expect(submit).toBeDisabled();
    });

    resolveResponse({
      ok: true,
      status: 200,
      json: async () => ({ user: { username: "user" }, csrf_token: "" }),
    });
  });
});

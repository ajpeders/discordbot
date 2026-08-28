import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import LoginPage from "./LoginPage";

const api = vi.hoisted(() => ({
  login: vi.fn(),
  getAuthConfig: vi.fn(),
  startDiscordLogin: vi.fn(),
  consumeTokenFromFragment: vi.fn(() => false),
  oauthErrorMessage: vi.fn((code: string | null) =>
    code === "not_a_member" ? "That Discord account isn't in this server." : "",
  ),
}));

const navigate = vi.hoisted(() => vi.fn());

vi.mock("../api/auth", () => ({ ...api }));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigate };
});

function renderAt(path = "/login") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <LoginPage />
    </MemoryRouter>,
  );
}

describe("LoginPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.consumeTokenFromFragment.mockReturnValue(false);
    api.getAuthConfig.mockResolvedValue({ password: true, discord: true });
  });

  it("offers both sign-in methods when both are configured", async () => {
    renderAt();

    expect(
      await screen.findByRole("button", { name: /continue with discord/i }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
  });

  it("hides the password form when only Discord is configured", async () => {
    api.getAuthConfig.mockResolvedValue({ password: false, discord: true });

    renderAt();

    expect(
      await screen.findByRole("button", { name: /continue with discord/i }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByLabelText(/password/i)).toBeNull(),
    );
  });

  it("hides the Discord button when OAuth is not configured", async () => {
    api.getAuthConfig.mockResolvedValue({ password: true, discord: false });

    renderAt();

    expect(await screen.findByLabelText(/password/i)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /continue with discord/i }),
    ).toBeNull();
  });

  it("starts the Discord handshake on click", async () => {
    const user = userEvent.setup();
    renderAt();

    await user.click(
      await screen.findByRole("button", { name: /continue with discord/i }),
    );

    expect(api.startDiscordLogin).toHaveBeenCalledOnce();
  });

  it("explains why a non-member was rejected", async () => {
    renderAt("/login?error=not_a_member");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /isn't in this server/i,
    );
  });

  it("consumes a token handed back in the fragment and leaves the page", async () => {
    api.consumeTokenFromFragment.mockReturnValue(true);

    renderAt();

    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith("/", { replace: true }),
    );
  });

  it("still offers the password form if the bot is unreachable", async () => {
    api.getAuthConfig.mockRejectedValue(new Error("down"));

    renderAt();

    expect(await screen.findByLabelText(/password/i)).toBeInTheDocument();
  });

  it("signs in with a password", async () => {
    const user = userEvent.setup();
    api.login.mockResolvedValue(undefined);

    renderAt();

    await user.type(await screen.findByLabelText(/password/i), "hunter2");
    await user.click(screen.getByRole("button", { name: /^sign in$/i }));

    await waitFor(() => expect(api.login).toHaveBeenCalledWith("hunter2"));
    expect(navigate).toHaveBeenCalledWith("/");
  });
});

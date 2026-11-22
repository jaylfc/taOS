import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { DockerConsentDialog } from "./DockerConsentDialog";
import { AppCard } from "./index";
import type { CatalogApp, InstallTarget } from "./types";
import userEvent from "@testing-library/user-event";

describe("DockerConsentDialog", () => {
  const defaultProps = {
    appId: "test-app",
    command: "sudo bash /opt/tinyagentos/scripts/install-server.sh --ensure-docker",
    message: "This app requires Docker to run. Run the following command to install Docker.",
    onAccept: vi.fn(),
    onDecline: vi.fn(),
  };

  it("renders the dialog with app name and command", () => {
    render(<DockerConsentDialog {...defaultProps} />);
    
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Docker Required")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toHaveAttribute("aria-label", "test-app requires Docker");
    expect(screen.getByText(/sudo bash/)).toBeInTheDocument();
    expect(screen.getByText("Agree & Install Docker")).toBeInTheDocument();
    expect(screen.getByText("Cancel")).toBeInTheDocument();
  });

  it("calls onAccept when Agree button is clicked", async () => {
    render(<DockerConsentDialog {...defaultProps} />);
    
    await userEvent.click(screen.getByText("Agree & Install Docker"));
    
    expect(defaultProps.onAccept).toHaveBeenCalledTimes(1);
  });

  it("calls onDecline when Cancel button is clicked", async () => {
    render(<DockerConsentDialog {...defaultProps} />);
    
    await userEvent.click(screen.getByText("Cancel"));
    
    expect(defaultProps.onDecline).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape key", async () => {
    render(<DockerConsentDialog {...defaultProps} />);
    
    fireEvent.keyDown(document, { key: "Escape" });
    
    expect(defaultProps.onDecline).toHaveBeenCalledTimes(1);
  });

  it("disables buttons when submitting", async () => {
    render(<DockerConsentDialog {...defaultProps} submitting={true} />);
    
    const agreeButton = screen.getByText("Installing Docker…");
    const cancelButton = screen.getByText("Cancel");
    
    expect(agreeButton).toBeDisabled();
    expect(cancelButton).toBeDisabled();
  });
});

describe("AppCard Docker badge", () => {
  const mockApp: CatalogApp = {
    id: "test-docker-app",
    name: "Test Docker App",
    type: "service",
    version: "1.0.0",
    description: "A test app that requires Docker",
    installed: false,
    compat: "green",
    runtime: "docker",
  };

  const mockInstallTargets: InstallTarget[] = [
    { name: "local", label: "Local", type: "local" },
  ];

  const renderAppCard = (app: CatalogApp = mockApp) => {
    return render(
      <AppCard
        app={app}
        affected={0}
        onInstall={vi.fn()}
        onUninstall={vi.fn()}
        installTargets={mockInstallTargets}
        runtimeHost={null}
      />
    );
  };

  it("shows 'Needs Docker' badge for docker runtime apps", () => {
    renderAppCard();
    
    expect(screen.getByText("Needs Docker")).toBeInTheDocument();
    expect(screen.getByText("Needs Docker")).toHaveAttribute("aria-label", "Test Docker App requires Docker");
  });

  it("does not show 'Needs Docker' badge for non-docker runtime apps", () => {
    renderAppCard({ ...mockApp, runtime: "lxc" });
    
    expect(screen.queryByText("Needs Docker")).not.toBeInTheDocument();
  });

  it("does not show 'Needs Docker' badge for installed apps", () => {
    renderAppCard({ ...mockApp, installed: true });
    
    expect(screen.queryByText("Needs Docker")).not.toBeInTheDocument();
  });

  it("shows 'Needs Docker' badge for either runtime (uses LXC by default)", () => {
    renderAppCard({ ...mockApp, runtime: "either" });
    
    expect(screen.queryByText("Needs Docker")).not.toBeInTheDocument();
  });
});
import Foundation
import NetworkExtension

/// Installs and starts the loopback VPN (the PacketTunnel extension).
@MainActor
final class TunnelManager: ObservableObject {
    static let providerBundleID = "dev.aravbhatt.spoofer.tunnel"

    @Published private(set) var status: NEVPNStatus = .invalid
    private var manager: NETunnelProviderManager?
    private var observer: NSObjectProtocol?

    var statusText: String {
        switch status {
        case .connected: return "On"
        case .connecting: return "Connecting…"
        case .disconnecting: return "Turning off…"
        case .reasserting: return "Reconnecting…"
        case .disconnected: return "Off"
        case .invalid: return "Not installed"
        @unknown default: return "Unknown"
        }
    }

    func load() async {
        let managers = (try? await NETunnelProviderManager.loadAllFromPreferences()) ?? []
        manager = managers.first
        observe()
        status = manager?.connection.status ?? .invalid
    }

    /// First call shows iOS's "Add VPN Configuration" prompt.
    func start() async throws {
        if manager == nil {
            let m = NETunnelProviderManager()
            let proto = NETunnelProviderProtocol()
            proto.providerBundleIdentifier = Self.providerBundleID
            proto.serverAddress = "Loopback (this iPhone)"
            m.protocolConfiguration = proto
            m.localizedDescription = "Spoofer Loopback"
            manager = m
        }
        guard let manager else { return }
        manager.isEnabled = true
        try await manager.saveToPreferences()
        try await manager.loadFromPreferences()  // required after saving, or the start fails
        observe()
        try manager.connection.startVPNTunnel()
    }

    func stop() {
        manager?.connection.stopVPNTunnel()
    }

    private func observe() {
        guard let manager, observer == nil else { return }
        observer = NotificationCenter.default.addObserver(
            forName: .NEVPNStatusDidChange, object: manager.connection, queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.status = self?.manager?.connection.status ?? .invalid }
        }
    }
}

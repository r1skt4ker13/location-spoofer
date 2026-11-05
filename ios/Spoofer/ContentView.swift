import Network
import SwiftUI

/// Milestone 1 test screen: can this app reach the phone's own developer services
/// (RemotePairing) through the loopback VPN, and does it still work with Wi-Fi off?
struct ContentView: View {
    @StateObject private var tunnel = TunnelManager()
    @StateObject private var network = NetworkWatcher()
    @State private var log: [LogLine] = []
    @State private var running = false
    @State private var holdTask: Task<Void, Never>?

    var body: some View {
        NavigationStack {
            List {
                Section("Network right now") {
                    LabeledContent("Wi-Fi", value: network.wifi)
                    LabeledContent("Cellular", value: network.cellular)
                    LabeledContent("Loopback VPN", value: tunnel.statusText)
                }

                Section {
                    Button(tunnel.status == .connected ? "Loopback VPN is on" : "1. Turn on loopback VPN") {
                        Task { await startVPN() }
                    }
                    .disabled(tunnel.status == .connected || tunnel.status == .connecting)

                    Button(running ? "Testing…" : "2. Run connection test") {
                        Task { await runTest() }
                    }
                    .disabled(running)

                    Button(holdTask == nil ? "3. Hold connection (then turn Wi-Fi off)" : "Stop holding") {
                        if let t = holdTask { t.cancel(); holdTask = nil } else { startHold() }
                    }
                    .disabled(running)
                } footer: {
                    Text("Test 2 with Wi-Fi on, then off. Test 3: start it with Wi-Fi ON, then turn Wi-Fi off in Settings and come back. It pings the phone every 10 s.")
                }

                Section("Results") {
                    if log.isEmpty { Text("No test run yet").foregroundStyle(.secondary) }
                    ForEach(log) { line in
                        Text(line.text)
                            .font(.system(.footnote, design: .monospaced))
                            .foregroundStyle(line.color)
                    }
                }
            }
            .navigationTitle("Spoofer – Test 1")
            .toolbar {
                if !log.isEmpty { Button("Clear") { log.removeAll() } }
            }
        }
        .task { await tunnel.load() }
    }

    private func add(_ text: String, _ color: Color = .primary) { log.append(LogLine(text: text, color: color)) }

    private func startVPN() async {
        do {
            try await tunnel.start()
        } catch {
            add("VPN failed to start: \(error.localizedDescription)", .red)
        }
    }

    private func runTest() async {
        running = true
        defer { running = false }
        let time = Date.now.formatted(date: .omitted, time: .standard)
        add("— Test at \(time) · Wi-Fi \(network.wifi) · Cellular \(network.cellular) · VPN \(tunnel.statusText)")

        // 1. Does the loopback VPN itself work? (our own echo server, reached through the VPN)
        do {
            let port = try await LoopbackSelfTest.run(peer: PacketTunnelAddresses.peer)
            add("VPN self-test: ✅ loopback works (echo via 10.7.0.1:\(port))", .green)
        } catch {
            add("VPN self-test: ❌ \(error.localizedDescription)", .red)
        }
        // 2. lockdownd (expected to hang up on iOS 26; kept for comparison).
        await probe(host: PacketTunnelAddresses.peer, label: "Lockdown (62078)")
        // 3. RemotePairing: the modern path to the developer tunnel.
        await probeRemotePairing()
    }

    private func probeRemotePairing() async {
        let client = RemotePairingClient(host: PacketTunnelAddresses.peer)
        defer { client.close() }
        do {
            try await client.connect()
            let info = try await client.handshake()
            let version = info["wireProtocolVersion"].map { "\($0)" } ?? "?"
            let tunnels = (info["deviceOptions"] as? [String: Any])?["allowsIncomingTunnelConnections"].map { "\($0)" } ?? "?"
            add("RemotePairing (49152): ✅ handshake OK (protocol v\(version), tunnels allowed: \(tunnels))", .green)
        } catch {
            add("RemotePairing (49152): ❌ \(error.localizedDescription)", .red)
        }
    }

    /// Open one RemotePairing connection while Wi-Fi is on, then watch it: logs every 10 s
    /// whether the phone still has it open. Shows whether an existing connection survives
    /// Wi-Fi being turned off. (The phone closes the connection on a second handshake,
    /// so this watches an idle connection instead of re-pinging.)
    private func startHold() {
        holdTask = Task {
            let client = RemotePairingClient(host: PacketTunnelAddresses.peer)
            defer { client.close(); holdTask = nil }
            add("— Hold started · Wi-Fi \(network.wifi)")
            do {
                try await client.connect()
                _ = try await client.handshake()
                add("Hold: ✅ connected + handshake. Now turn Wi-Fi off and come back.", .green)
            } catch {
                add("Hold: ❌ couldn't start: \(error.localizedDescription)", .red)
                return
            }
            let closeReason = CloseFlag()
            let watcher = Task.detached { closeReason.set(await client.waitForClose()) }
            let start = Date.now
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(10))
                let secs = Int(Date.now.timeIntervalSince(start))
                if let reason = closeReason.value {
                    add("Hold: ❌ ended after \(secs)s · Wi-Fi \(network.wifi) · \(reason)", .red)
                    return
                }
                add("Hold \(secs)s · Wi-Fi \(network.wifi) · Cellular \(network.cellular): ✅ still open", .green)
            }
            watcher.cancel()
            add("Hold stopped")
        }
    }

    private func probe(host: String, label: String) async {
        let client = LockdownClient(host: host)
        defer { client.close() }
        do {
            try await client.connect()
            let type = try await client.request("QueryType")
            guard let t = type["Type"] as? String else {
                add("\(label): connected but odd reply \(type)", .orange)
                return
            }
            add("\(label): ✅ lockdown answered (\(t))", .green)
            for key in ["DeviceName", "ProductVersion"] {
                let r = try await client.request("GetValue", ["Key": key])
                if let v = r["Value"] { add("   \(key) = \(v)", .green) }
                else { add("   \(key): \(r["Error"] as? String ?? "no value") (expected without a session)", .secondary) }
            }
        } catch {
            add("\(label): ❌ \(error.localizedDescription)", .red)
        }
    }
}

/// The loopback VPN's fake peer address (must match PacketTunnelProvider.peerAddress).
enum PacketTunnelAddresses {
    static let peer = "10.7.0.1"
}

struct LogLine: Identifiable {
    let id = UUID()
    let text: String
    let color: Color
}

/// Reports whether Wi-Fi and cellular are currently usable.
@MainActor
final class NetworkWatcher: ObservableObject {
    @Published var wifi = "?"
    @Published var cellular = "?"
    private let monitor = NWPathMonitor()

    init() {
        monitor.pathUpdateHandler = { [weak self] path in
            let wifi = path.availableInterfaces.contains { $0.type == .wifi } ? "connected" : "off / not joined"
            let cell = path.availableInterfaces.contains { $0.type == .cellular } ? "connected" : "none"
            Task { @MainActor in
                self?.wifi = wifi
                self?.cellular = cell
            }
        }
        monitor.start(queue: DispatchQueue(label: "net-watch"))
    }
}

/// Set once by the close-watcher, read by the hold loop.
final class CloseFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var reason: String?
    var value: String? { lock.lock(); defer { lock.unlock() }; return reason }
    func set(_ r: String) { lock.lock(); reason = r; lock.unlock() }
}

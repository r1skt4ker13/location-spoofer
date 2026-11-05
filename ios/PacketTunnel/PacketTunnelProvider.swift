import NetworkExtension
import os

/// Loopback VPN: lets the app talk to this iPhone's own lockdown/developer services
/// as if it were a computer on the network.
///
/// The tunnel claims `deviceAddress` and routes 10.7.0.0/24 (which includes the fake
/// computer, `peerAddress`) into itself. Every IPv4 packet that comes out of the tunnel gets its source and destination
/// swapped and is written straight back in. So:
///
///   app  10.7.0.0:x  ->  10.7.0.1:62078   (enters tunnel)
///        swapped:  10.7.0.1:x -> 10.7.0.0:62078   (delivered to lockdownd, which sees a "remote" peer)
///   lockdownd reply 10.7.0.0:62078 -> 10.7.0.1:x  (enters tunnel)
///        swapped:  10.7.0.1:62078 -> 10.7.0.0:x   (delivered back to the app's socket)
///
/// Swapping two addresses leaves the IPv4 header checksum and the TCP/UDP pseudo-header
/// checksums unchanged (they're commutative sums), so no checksum recomputation is needed.
final class PacketTunnelProvider: NEPacketTunnelProvider {
    static let deviceAddress = "10.7.0.0"
    static let peerAddress = "10.7.0.1"

    private let log = Logger(subsystem: "dev.aravbhatt.spoofer", category: "tunnel")

    override func startTunnel(options: [String: NSObject]?, completionHandler: @escaping (Error?) -> Void) {
        let settings = NEPacketTunnelNetworkSettings(tunnelRemoteAddress: Self.peerAddress)
        let ipv4 = NEIPv4Settings(addresses: [Self.deviceAddress], subnetMasks: ["255.255.255.0"])
        // Only 10.7.0.0/24 goes through the tunnel; all other traffic is untouched.
        ipv4.includedRoutes = [NEIPv4Route(destinationAddress: Self.deviceAddress, subnetMask: "255.255.255.0")]
        settings.ipv4Settings = ipv4
        settings.mtu = 1500

        setTunnelNetworkSettings(settings) { [weak self] error in
            if let error {
                self?.log.error("setTunnelNetworkSettings failed: \(error.localizedDescription)")
                completionHandler(error)
                return
            }
            self?.log.info("loopback tunnel up")
            completionHandler(nil)
            self?.readLoop()
        }
    }

    override func stopTunnel(with reason: NEProviderStopReason, completionHandler: @escaping () -> Void) {
        log.info("tunnel stopping, reason \(reason.rawValue)")
        completionHandler()
    }

    private func readLoop() {
        packetFlow.readPackets { [weak self] packets, protocols in
            guard let self else { return }
            self.packetFlow.writePackets(packets.map(Self.swapAddresses), withProtocols: protocols)
            self.readLoop()
        }
    }

    /// Swap IPv4 source (bytes 12-15) and destination (bytes 16-19). Non-IPv4 passes through.
    static func swapAddresses(_ packet: Data) -> Data {
        guard packet.count >= 20, packet[packet.startIndex] >> 4 == 4 else { return packet }
        var p = packet
        let s = p.startIndex
        for i in 0..<4 {
            let tmp = p[s + 12 + i]
            p[s + 12 + i] = p[s + 16 + i]
            p[s + 16 + i] = tmp
        }
        return p
    }
}

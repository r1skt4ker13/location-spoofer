import Foundation
import Network

/// Proves the loopback VPN itself works, independent of lockdownd: start an echo server
/// in this app, then connect to it through the VPN's fake peer address. If this passes
/// but lockdownd is refused, the VPN is fine and lockdownd is the one saying no.
enum LoopbackSelfTest {
    enum SelfTestError: LocalizedError {
        case listener(String), noPort, timeout, mismatch(String)
        var errorDescription: String? {
            switch self {
            case .listener(let m): return "Echo server failed: \(m)"
            case .noPort: return "Echo server got no port"
            case .timeout: return "Timed out"
            case .mismatch(let m): return "Unexpected reply: \(m)"
            }
        }
    }

    static func run(peer: String) async throws -> UInt16 {
        let queue = DispatchQueue(label: "selftest")
        let listener = try NWListener(using: .tcp, on: .any)
        defer { listener.cancel() }

        // Echo server: send back whatever arrives.
        listener.newConnectionHandler = { conn in
            conn.start(queue: queue)
            conn.receive(minimumIncompleteLength: 1, maximumLength: 64) { data, _, _, _ in
                conn.send(content: data, completion: .contentProcessed { _ in conn.cancel() })
            }
        }
        let port: UInt16 = try await withCheckedThrowingContinuation { cont in
            let once = ResumeOnce()
            listener.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    if once.claim() {
                        if let p = listener.port?.rawValue { cont.resume(returning: p) }
                        else { cont.resume(throwing: SelfTestError.noPort) }
                    }
                case .failed(let e):
                    if once.claim() { cont.resume(throwing: SelfTestError.listener(e.localizedDescription)) }
                default: break
                }
            }
            listener.start(queue: queue)
        }

        // Client: connect to the fake peer; the VPN swaps addresses so it lands on our server.
        let conn = NWConnection(host: NWEndpoint.Host(peer), port: NWEndpoint.Port(rawValue: port)!, using: .tcp)
        defer { conn.cancel() }
        let reply: String = try await withCheckedThrowingContinuation { cont in
            let once = ResumeOnce()
            conn.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    conn.send(content: Data("ping".utf8), completion: .contentProcessed { _ in })
                    conn.receive(minimumIncompleteLength: 1, maximumLength: 64) { data, _, _, error in
                        guard once.claim() else { return }
                        if let error { cont.resume(throwing: error) }
                        else { cont.resume(returning: String(decoding: data ?? Data(), as: UTF8.self)) }
                    }
                case .failed(let e), .waiting(let e):
                    if once.claim() { cont.resume(throwing: e) }
                default: break
                }
            }
            conn.start(queue: queue)
            queue.asyncAfter(deadline: .now() + 6) { if once.claim() { cont.resume(throwing: SelfTestError.timeout) } }
        }
        guard reply == "ping" else { throw SelfTestError.mismatch(reply) }
        return port
    }
}

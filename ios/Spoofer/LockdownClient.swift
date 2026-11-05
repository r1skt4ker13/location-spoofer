import Foundation
import Network

final class ResumeOnce: @unchecked Sendable {
    private let lock = NSLock()
    private var done = false
    func claim() -> Bool {
        lock.lock(); defer { lock.unlock() }
        if done { return false }
        done = true
        return true
    }
}

/// Minimal client for lockdownd, the iPhone's device-services "front door" (TCP port 62078).
///
/// Wire format: each message is a 4-byte big-endian length followed by a plist (XML or
/// binary). Requests are dictionaries with a "Request" key, e.g. {"Request": "QueryType"}.
/// Milestone 1 only needs unauthenticated requests; StartSession + TLS comes next.
final class LockdownClient {
    static let port: NWEndpoint.Port = 62078
    static let label = "Spoofer"

    enum LockdownError: LocalizedError {
        case timeout, closed, badMessage(String), connectFailed(String)
        var errorDescription: String? {
            switch self {
            case .timeout: return "Timed out"
            case .closed: return "Connection closed by the phone"
            case .badMessage(let m): return "Bad message: \(m)"
            case .connectFailed(let m): return "Couldn't connect: \(m)"
            }
        }
    }

    private let connection: NWConnection
    private let queue = DispatchQueue(label: "lockdown")

    init(host: String) {
        let tcp = NWProtocolTCP.Options()
        tcp.connectionTimeout = 5
        let params = NWParameters(tls: nil, tcp: tcp)
        connection = NWConnection(host: NWEndpoint.Host(host), port: Self.port, using: params)
    }

    func connect() async throws {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            // The state handler and the timeout both race to finish; resume exactly once.
            let once = ResumeOnce()
            let finish: @Sendable (Result<Void, Error>) -> Void = { result in
                if once.claim() { cont.resume(with: result) }
            }
            connection.stateUpdateHandler = { state in
                switch state {
                case .ready: finish(.success(()))
                case .failed(let e): finish(.failure(LockdownError.connectFailed(e.localizedDescription)))
                case .waiting(let e): finish(.failure(LockdownError.connectFailed(e.localizedDescription)))
                case .cancelled: finish(.failure(LockdownError.closed))
                default: break
                }
            }
            connection.start(queue: queue)
            queue.asyncAfter(deadline: .now() + 8) { finish(.failure(LockdownError.timeout)) }
        }
    }

    func close() { connection.cancel() }

    /// Send one request and wait for its reply.
    func request(_ name: String, _ extra: [String: Any] = [:]) async throws -> [String: Any] {
        var msg: [String: Any] = ["Label": Self.label, "Request": name]
        msg.merge(extra) { _, new in new }
        let body = try PropertyListSerialization.data(fromPropertyList: msg, format: .xml, options: 0)
        var frame = withUnsafeBytes(of: UInt32(body.count).bigEndian) { Data($0) }
        frame.append(body)
        try await send(frame)

        let header = try await receive(exactly: 4)
        let length = header.reduce(0) { ($0 << 8) | Int($1) }
        guard length > 0, length < 16 << 20 else { throw LockdownError.badMessage("length \(length)") }
        let reply = try await receive(exactly: length)
        guard let dict = try PropertyListSerialization.propertyList(from: reply, format: nil) as? [String: Any] else {
            throw LockdownError.badMessage("not a dictionary")
        }
        return dict
    }

    private func send(_ data: Data) async throws {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            connection.send(content: data, completion: .contentProcessed { error in
                if let error { cont.resume(throwing: error) } else { cont.resume() }
            })
        }
    }

    private func receive(exactly count: Int) async throws -> Data {
        var buffer = Data()
        while buffer.count < count {
            let chunk: Data = try await withCheckedThrowingContinuation { cont in
                connection.receive(minimumIncompleteLength: 1, maximumLength: count - buffer.count) { data, _, isComplete, error in
                    if let error { cont.resume(throwing: error) }
                    else if let data, !data.isEmpty { cont.resume(returning: data) }
                    else if isComplete { cont.resume(throwing: LockdownError.closed) }
                    else { cont.resume(returning: Data()) }
                }
            }
            buffer.append(chunk)
        }
        return buffer
    }
}

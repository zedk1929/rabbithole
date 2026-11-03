import Foundation
import Combine

class ServerManager: ObservableObject {
    static let shared = ServerManager()

    @Published var isServerRunning: Bool = false
    @Published var statusMessage: String = "Connecting to engine..."
    @Published var serverHost: String {
        didSet {
            UserDefaults.standard.set(serverHost, forKey: "rabbithole_server_host")
            recheck()
        }
    }

    private var healthTimer: Timer?
    private let defaultHost = "http://192.168.1.231:8000"

    init() {
        let savedHost = UserDefaults.standard.string(forKey: "rabbithole_server_host")
        self.serverHost = (savedHost != nil && !savedHost!.isEmpty) ? savedHost! : defaultHost

        checkHealth { [weak self] isHealthy in
            DispatchQueue.main.async {
                self?.isServerRunning = isHealthy
                self?.statusMessage = isHealthy ? "Connected" : "Engine Offline"
            }
        }
        startHealthMonitoring()
    }

    var serverURL: URL {
        URL(string: serverHost) ?? URL(string: defaultHost)!
    }

    var healthURL: URL {
        URL(string: "\(serverHost.trimmingCharacters(in: CharacterSet(charactersIn: "/")))/api/health") ?? URL(string: "\(defaultHost)/api/health")!
    }

    func checkHealth(completion: @escaping (Bool) -> Void) {
        var request = URLRequest(url: healthURL)
        request.timeoutInterval = 1.5

        URLSession.shared.dataTask(with: request) { _, response, error in
            if let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 {
                completion(true)
            } else {
                completion(false)
            }
        }.resume()
    }

    func recheck() {
        statusMessage = "Connecting..."
        checkHealth { [weak self] isHealthy in
            DispatchQueue.main.async {
                self?.isServerRunning = isHealthy
                self?.statusMessage = isHealthy ? "Connected" : "Engine Offline"
            }
        }
    }

    func resetToDefaultHost() {
        self.serverHost = defaultHost
    }

    private func startHealthMonitoring() {
        healthTimer?.invalidate()
        healthTimer = Timer.scheduledTimer(withTimeInterval: 4.0, repeats: true) { [weak self] _ in
            self?.checkHealth { isHealthy in
                DispatchQueue.main.async {
                    if self?.isServerRunning != isHealthy {
                        self?.isServerRunning = isHealthy
                        self?.statusMessage = isHealthy ? "Connected" : "Engine Offline"
                    }
                }
            }
        }
    }

    deinit {
        healthTimer?.invalidate()
    }
}

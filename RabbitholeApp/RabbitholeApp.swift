import SwiftUI

@main
struct RabbitholeApp: App {
    @StateObject private var serverManager = ServerManager.shared

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(serverManager)
                .preferredColorScheme(.dark)
        }
    }
}

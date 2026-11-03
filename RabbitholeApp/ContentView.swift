import SwiftUI
import WebKit

#if os(iOS)
import UIKit
#endif

struct ContentView: View {
    @EnvironmentObject var serverManager: ServerManager
    @State private var webView: WKWebView?
    @State private var canGoBack: Bool = false
    @State private var canGoForward: Bool = false
    @State private var isLoading: Bool = false
    @State private var showSettingsSheet: Bool = false
    @State private var hostInput: String = ""

    var body: some View {
        ZStack {
            Color(red: 9/255, green: 9/255, blue: 11/255)
                .ignoresSafeArea()

            VStack(spacing: 0) {
                // Mobile Top Navigation Bar
                HStack(spacing: 10) {
                    HStack(spacing: 8) {
                        Image("AppLogo")
                            .resizable()
                            .aspectRatio(contentMode: .fit)
                            .frame(width: 26, height: 26)
                            .cornerRadius(6)

                        Text("Rabbithole")
                            .font(.system(size: 16, weight: .bold))
                            .foregroundColor(.white)
                    }

                    Spacer()

                    // Server Status Indicator Button
                    Button(action: {
                        hostInput = serverManager.serverHost
                        showSettingsSheet = true
                    }) {
                        HStack(spacing: 5) {
                            Circle()
                                .fill(serverManager.isServerRunning ? Color(red: 16/255, green: 185/255, blue: 129/255) : Color(red: 245/255, green: 158/255, blue: 11/255))
                                .frame(width: 7, height: 7)

                            Text(serverManager.statusMessage)
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundColor(.white.opacity(0.85))
                        }
                        .padding(.horizontal, 9)
                        .padding(.vertical, 4.5)
                        .background(Color.white.opacity(0.08))
                        .cornerRadius(12)
                    }
                    .buttonStyle(.plain)
                }
                .padding(.horizontal, 16)
                .padding(.top, 8)
                .padding(.bottom, 10)
                .background(Color(red: 14/255, green: 14/255, blue: 18/255))
                .overlay(
                    Rectangle()
                        .frame(height: 1)
                        .foregroundColor(Color.white.opacity(0.08)),
                    alignment: .bottom
                )

                // Loading Progress Bar
                if isLoading {
                    ProgressView()
                        .progressViewStyle(LinearProgressViewStyle(tint: Color(red: 16/255, green: 185/255, blue: 129/255)))
                        .frame(height: 2)
                }

                // Main Content
                if serverManager.isServerRunning {
                    WebView(
                        url: serverManager.serverURL,
                        webViewReference: $webView,
                        canGoBack: $canGoBack,
                        canGoForward: $canGoForward,
                        isLoading: $isLoading
                    )
                    .edgesIgnoringSafeArea(.horizontal)
                } else {
                    offlineView
                }

                // Mobile Bottom Toolbar
                HStack(spacing: 0) {
                    // Back
                    Button(action: {
                        triggerHaptic()
                        webView?.goBack()
                    }) {
                        Image(systemName: "chevron.left")
                            .font(.system(size: 16, weight: .semibold))
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                    }
                    .disabled(!canGoBack)
                    .foregroundColor(canGoBack ? .white : .gray.opacity(0.3))

                    // Forward
                    Button(action: {
                        triggerHaptic()
                        webView?.goForward()
                    }) {
                        Image(systemName: "chevron.right")
                            .font(.system(size: 16, weight: .semibold))
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                    }
                    .disabled(!canGoForward)
                    .foregroundColor(canGoForward ? .white : .gray.opacity(0.3))

                    // Reload
                    Button(action: {
                        triggerHaptic()
                        if serverManager.isServerRunning {
                            webView?.reload()
                        } else {
                            serverManager.recheck()
                        }
                    }) {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 15, weight: .semibold))
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                    }
                    .foregroundColor(.white.opacity(0.9))

                    // Safari
                    Button(action: {
                        triggerHaptic()
                        #if os(iOS)
                        UIApplication.shared.open(serverManager.serverURL)
                        #elseif os(macOS)
                        NSWorkspace.shared.open(serverManager.serverURL)
                        #endif
                    }) {
                        Image(systemName: "safari")
                            .font(.system(size: 16, weight: .regular))
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                    }
                    .foregroundColor(.white.opacity(0.75))

                    // Server Settings
                    Button(action: {
                        triggerHaptic()
                        hostInput = serverManager.serverHost
                        showSettingsSheet = true
                    }) {
                        Image(systemName: "network")
                            .font(.system(size: 15, weight: .regular))
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                    }
                    .foregroundColor(.white.opacity(0.75))
                }
                .padding(.horizontal, 8)
                .background(Color(red: 14/255, green: 14/255, blue: 18/255))
                .overlay(
                    Rectangle()
                        .frame(height: 1)
                        .foregroundColor(Color.white.opacity(0.08)),
                    alignment: .top
                )
            }
        }
        .sheet(isPresented: $showSettingsSheet) {
            settingsSheetView
        }
    }

    private var offlineView: some View {
        VStack(spacing: 20) {
            Spacer()

            ZStack {
                Circle()
                    .fill(Color(red: 245/255, green: 158/255, blue: 11/255).opacity(0.12))
                    .frame(width: 80, height: 80)

                Image(systemName: "wifi.exclamationmark")
                    .font(.system(size: 32, weight: .semibold))
                    .foregroundColor(Color(red: 245/255, green: 158/255, blue: 11/255))
            }

            VStack(spacing: 8) {
                Text("Connecting to Engine...")
                    .font(.system(size: 19, weight: .bold))
                    .foregroundColor(.white)

                Text("Target Host: \(serverManager.serverHost)\nEnsure your Mac's backend is running:\nuv run run.py")
                    .font(.system(size: 13, weight: .regular))
                    .multilineTextAlignment(.center)
                    .foregroundColor(.gray)
                    .lineSpacing(4)
                    .padding(.horizontal, 24)
            }

            VStack(spacing: 12) {
                Button(action: {
                    triggerHaptic()
                    serverManager.recheck()
                }) {
                    HStack(spacing: 8) {
                        Image(systemName: "arrow.triangle.2.circlepath")
                        Text("Retry Connection")
                    }
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.black)
                    .padding(.horizontal, 24)
                    .padding(.vertical, 12)
                    .background(Color(red: 16/255, green: 185/255, blue: 129/255))
                    .cornerRadius(8)
                }

                Button(action: {
                    hostInput = serverManager.serverHost
                    showSettingsSheet = true
                }) {
                    Text("Change Host / IP")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundColor(Color(red: 16/255, green: 185/255, blue: 129/255))
                }
            }
            .padding(.top, 8)

            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var settingsSheetView: some View {
        NavigationView {
            Form {
                Section(header: Text("Engine Host Address")) {
                    TextField("http://192.168.1.231:8000", text: $hostInput)
                        .autocapitalization(.none)
                        .disableAutocorrection(true)
                        .keyboardType(.URL)

                    Text("Enter your Mac's local Wi-Fi IP (e.g. 'http://192.168.1.231:8000') so your iPhone can reach the engine on your home Wi-Fi.")
                        .font(.system(size: 12))
                        .foregroundColor(.gray)
                }

                Section {
                    Button("Save & Connect") {
                        triggerHaptic()
                        serverManager.serverHost = hostInput.trimmingCharacters(in: .whitespacesAndNewlines)
                        showSettingsSheet = false
                    }
                    .foregroundColor(Color(red: 16/255, green: 185/255, blue: 129/255))

                    Button("Reset to Default (192.168.1.231:8000)") {
                        triggerHaptic()
                        serverManager.resetToDefaultHost()
                        hostInput = serverManager.serverHost
                    }
                    .foregroundColor(.orange)
                }
            }
            .navigationTitle("Connection Settings")
            .navigationBarItems(trailing: Button("Done") {
                showSettingsSheet = false
            })
        }
        .preferredColorScheme(.dark)
    }

    private func triggerHaptic() {
        #if os(iOS)
        let impact = UIImpactFeedbackGenerator(style: .light)
        impact.impactOccurred()
        #endif
    }
}

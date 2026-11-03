import SwiftUI
import WebKit

#if os(iOS)
import UIKit
typealias PlatformViewRepresentable = UIViewRepresentable
#elseif os(macOS)
import AppKit
typealias PlatformViewRepresentable = NSViewRepresentable
#endif

struct WebView: PlatformViewRepresentable {
    let url: URL
    @Binding var webViewReference: WKWebView?
    @Binding var canGoBack: Bool
    @Binding var canGoForward: Bool
    @Binding var isLoading: Bool

    func makeCoordinator() -> Coordinator {
        Coordinator(self)
    }

#if os(iOS)
    func makeUIView(context: Context) -> WKWebView {
        createWebView(context: context)
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {
        // Handled via coordinator and direct webView calls
    }
#elseif os(macOS)
    func makeNSView(context: Context) -> WKWebView {
        createWebView(context: context)
    }

    func updateNSView(_ nsView: WKWebView, context: Context) {
        // Handled via coordinator and direct webView calls
    }
#endif

    private func createWebView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []

        #if os(iOS)
        config.defaultWebpagePreferences.allowsContentJavaScript = true
        #elseif os(macOS)
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")
        #endif

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = true

        #if os(iOS)
        webView.isOpaque = false
        webView.backgroundColor = .clear
        webView.scrollView.backgroundColor = UIColor(red: 9/255, green: 9/255, blue: 11/255, alpha: 1)
        webView.scrollView.indicatorStyle = .white
        webView.scrollView.contentInsetAdjustmentBehavior = .automatic

        // Native iOS pull-to-refresh
        let refreshControl = UIRefreshControl()
        refreshControl.tintColor = UIColor(red: 16/255, green: 185/255, blue: 129/255, alpha: 1)
        refreshControl.addTarget(context.coordinator, action: #selector(Coordinator.handleRefreshControl(_:)), for: .valueChanged)
        webView.scrollView.refreshControl = refreshControl
        context.coordinator.refreshControl = refreshControl
        #elseif os(macOS)
        webView.setValue(false, forKey: "drawsBackground")
        #endif

        context.coordinator.setupObservers(for: webView)
        DispatchQueue.main.async {
            self.webViewReference = webView
        }

        var request = URLRequest(url: url)
        request.cachePolicy = .useProtocolCachePolicy
        webView.load(request)

        return webView
    }

    class Coordinator: NSObject, WKNavigationDelegate {
        var parent: WebView
        private var kvoTokens: [NSKeyValueObservation] = []
        #if os(iOS)
        weak var refreshControl: UIRefreshControl?
        #endif

        init(_ parent: WebView) {
            self.parent = parent
        }

        #if os(iOS)
        @objc func handleRefreshControl(_ sender: UIRefreshControl) {
            parent.webViewReference?.reload()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) {
                sender.endRefreshing()
            }
        }
        #endif

        func setupObservers(for webView: WKWebView) {
            kvoTokens.append(webView.observe(\.canGoBack, options: [.new]) { [weak self] wv, _ in
                DispatchQueue.main.async {
                    self?.parent.canGoBack = wv.canGoBack
                }
            })
            kvoTokens.append(webView.observe(\.canGoForward, options: [.new]) { [weak self] wv, _ in
                DispatchQueue.main.async {
                    self?.parent.canGoForward = wv.canGoForward
                }
            })
            kvoTokens.append(webView.observe(\.isLoading, options: [.new]) { [weak self] wv, _ in
                DispatchQueue.main.async {
                    self?.parent.isLoading = wv.isLoading
                }
            })
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            DispatchQueue.main.async {
                self.parent.isLoading = false
                #if os(iOS)
                self.refreshControl?.endRefreshing()
                #endif
            }
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            DispatchQueue.main.async {
                self.parent.isLoading = false
                #if os(iOS)
                self.refreshControl?.endRefreshing()
                #endif
            }
        }
    }
}

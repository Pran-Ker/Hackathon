import SwiftUI
import WebKit

struct HologramView: View {
    let scene: Scene3D
    let baseURL: String
    let demoMode: Bool
    let onClose: () -> Void

    @StateObject private var webStore = WebViewStore()
    @State private var loadFailed = false
    @State private var savedToast = false

    private var sceneID: String? { scene.resolvedSceneID }

    private var viewerURL: URL? {
        guard let sceneID else { return nil }
        return UploadClient.viewerURL(base: baseURL, sceneID: sceneID)
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            if let viewerURL, !loadFailed {
                WebView(store: webStore, url: viewerURL, onFail: { loadFailed = true })
                    .ignoresSafeArea(edges: .bottom)
            } else {
                fallback
            }

            if savedToast {
                VStack {
                    Spacer()
                    Text("Saved to Photos")
                        .font(.footnote.bold())
                        .padding(.horizontal, 16)
                        .padding(.vertical, 8)
                        .background(.ultraThinMaterial, in: Capsule())
                        .padding(.bottom, 32)
                }
                .transition(.opacity)
            }
        }
        .navigationTitle(scene.prompt ?? "Hologram")
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden(true)
        .toolbarBackground(.black, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button("New Scan") { onClose() }
            }
            ToolbarItemGroup(placement: .topBarTrailing) {
                if let viewerURL {
                    ShareLink(item: viewerURL) {
                        Image(systemName: "square.and.arrow.up")
                    }
                }
                if !loadFailed && viewerURL != nil {
                    Button {
                        saveSnapshot()
                    } label: {
                        Image(systemName: "camera.viewfinder")
                    }
                }
            }
        }
    }

    private var fallback: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack(spacing: 10) {
                    Image(systemName: demoMode ? "sparkles" : "wifi.exclamationmark")
                        .foregroundStyle(.yellow)
                    Text(demoMode
                         ? "Demo scene (bundled) — start the POPFRAME server to see the live 3D viewer."
                         : "The 3D viewer couldn't load from \(UploadClient.normalizedBase(baseURL)). Showing scene data instead.")
                        .font(.footnote)
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.white.opacity(0.1), in: RoundedRectangle(cornerRadius: 12))

                if sceneID == nil {
                    Text("No scene id in the response — the viewer URL can't be built. (Backend should return scene_id.)")
                        .font(.footnote)
                        .foregroundStyle(.orange)
                }

                if let prompt = scene.prompt {
                    LabeledContent("Prompt", value: prompt)
                }
                if let cached = scene.cached {
                    LabeledContent("Cached", value: cached ? "yes" : "no")
                }
                if let timings = scene.timings, !timings.isEmpty {
                    LabeledContent("Timings",
                                   value: timings.sorted(by: { $0.key < $1.key })
                                       .map { "\($0.key) \(String(format: "%.1f", $0.value))s" }
                                       .joined(separator: ", "))
                }

                Text("Objects (\(scene.objects.count))")
                    .font(.headline)
                    .padding(.top, 4)

                ForEach(scene.objects) { object in
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text(object.displayName).font(.subheadline.bold())
                            Spacer()
                            if let score = object.score {
                                Text(String(format: "%.0f%%", score * 100))
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        if let tags = object.info?.tags, !tags.isEmpty {
                            Text(tags.joined(separator: " · "))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        if let facts = object.info?.facts {
                            ForEach(facts, id: \.self) { fact in
                                Text("• \(fact)").font(.caption)
                            }
                        }
                    }
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
                }

                if !demoMode {
                    Button {
                        loadFailed = false
                    } label: {
                        Label("Retry viewer", systemImage: "arrow.clockwise")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .padding(.top, 8)
                }
            }
            .padding()
        }
        .foregroundStyle(.white)
    }

    private func saveSnapshot() {
        webStore.webView.takeSnapshot(with: nil) { image, _ in
            guard let image else { return }
            UIImageWriteToSavedPhotosAlbum(image, nil, nil, nil)
            withAnimation { savedToast = true }
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                withAnimation { savedToast = false }
            }
        }
    }
}

final class WebViewStore: ObservableObject {
    let webView: WKWebView

    init() {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []
        config.websiteDataStore = .default()
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.isOpaque = false
        webView.backgroundColor = .black
        webView.scrollView.backgroundColor = .black
        self.webView = webView
    }
}

struct WebView: UIViewRepresentable {
    let store: WebViewStore
    let url: URL
    let onFail: () -> Void

    func makeUIView(context: Context) -> WKWebView {
        let webView = store.webView
        webView.navigationDelegate = context.coordinator
        webView.load(URLRequest(url: url, timeoutInterval: 30))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(onFail: onFail) }

    final class Coordinator: NSObject, WKNavigationDelegate {
        let onFail: () -> Void
        init(onFail: @escaping () -> Void) { self.onFail = onFail }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            onFail()
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            onFail()
        }
    }
}

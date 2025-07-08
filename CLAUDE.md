# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## プロジェクト概要

Ableton Live MCP Serverは、Model Context Protocol (MCP) を実装したサーバーで、LLMとAbleton Liveの間の通信を可能にします。OSC (Open Sound Control) プロトコルを使用してAbleton Liveとメッセージの送受信を行います。

## アーキテクチャ

### 通信フロー
1. MCPクライアント（例：Claude Desktop）→ MCPサーバー（mcp_ableton_server.py）
2. MCPサーバー → OSCデーモン（osc_daemon.py）via TCPソケット（ポート65432）
3. OSCデーモン → Ableton Live via OSCプロトコル（ポート11000）
4. Ableton Live → OSCデーモン（ポート11001）→ MCPサーバー → MCPクライアント

### 主要コンポーネント

- **mcp_ableton_server.py**: FastMCPフレームワークを使用したMCPサーバー
  - AbletonClientクラス：非同期ソケット通信とJSON-RPCプロトコルの実装
  - 5つのMCPツールを実装（get_track_names, add_midi_notes, remove_midi_notes, create_midi_clip, create_drum_pattern）

- **osc_daemon.py**: OSCデーモンとしてMCPとAbleton Liveをブリッジ
  - 非同期I/Oによる並行処理
  - レスポンスタイムアウト管理（5秒）
  - JSON-RPCプロトコルの実装

## 開発コマンド

### セットアップと実行
```bash
# 依存関係のインストール
uv sync

# OSCデーモンの起動（最初に必要）
uv run osc_daemon.py

# MCPサーバーの起動（別ターミナルで）
uv run mcp_ableton_server.py
```

### テスト
- メインプロジェクトには専用のテストファイルはありません
- AbletonOSCサブディレクトリにはpytestを使用したテストスイートがあります
- AbletonOSCのテストを実行する場合：
  ```bash
  cd AbletonOSC
  pytest tests/
  ```

## 重要な実装詳細

### エラーハンドリング
- OSCデーモンは5秒のタイムアウトを持ち、応答がない場合はタイムアウトエラーを返します
- AbletonClientは非同期でレスポンスを読み取り、request_idで照合します

### JSON-RPCプロトコル
- リクエスト形式：`{"jsonrpc": "2.0", "id": "1", "method": "send_osc", "params": {"address": "/live/...", "args": [...]}}`
- レスポンス形式：`{"jsonrpc": "2.0", "id": "1", "result": {...}}` または `{"jsonrpc": "2.0", "id": "1", "error": {...}}`

### MCPツール実装時の注意点
- 各ツールはAbletonClientのsend_osc_messageメソッドを使用してOSCメッセージを送信
- レスポンスはJSON形式で返されるため、適切にパースして処理する必要があります
- トラックやクリップのインデックスは0ベースです

## デバッグとトラブルシューティング

### ログ出力
- OSCデーモンは詳細なログを標準出力に出力します
- MCPサーバーはエラーを標準エラー出力に出力します

### 接続確認
1. OSCデーモンが起動していることを確認（ポート65432でリッスン）
2. Ableton LiveでAbletonOSCコントロールサーフェスが有効になっていることを確認
3. ポート番号が正しく設定されていることを確認（送信：11000、受信：11001）

## 今後の拡張時の指針

新しいMCPツールを追加する場合：
1. mcp_ableton_server.pyに新しいツール関数を追加
2. AbletonOSCのドキュメントを参照して適切なOSCアドレスを使用
3. レスポンスの形式を確認し、適切にパースして返す
/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

"use strict";

// reverse8 phase12: engine-level initiator stack capture (zero page-world pollution).
//
// Firefox broadcasts "http-on-opening-request" synchronously inside
// HttpChannelChild::AsyncOpen (netwerk/protocol/http/HttpChannelChild.cpp) —
// explicitly so that DevTools can capture a stack trace at that spot. At that
// moment the page JS that issued the fetch/XHR is still on the JS stack, so a
// chrome-privileged observer in the content process can read Components.stack
// and attribute the request to its initiator without wrapping, replacing, or
// even touching any page-world object. This mirrors
// devtools/server/actors/resources/network-events-stacktraces.js.
//
// Frames are filtered to non-chrome filenames (page http(s) frames), matching
// DevTools' NetworkUtils.removeChromeFrames semantics, then forwarded to the
// parent process via ppmm, keyed by channelId. The parent-side NetworkObserver
// merges them into Network.requestWillBeSent; channelId is identical across
// the content/parent boundary (HttpChannelChild sends it in openArgs and the
// parent adopts it via SetChannelId).
//
// Boundaries (inherited from the DevTools mechanism, by design):
// - Requests whose channel opens with no page JS on the stack (browser UI,
//   parent-process loads, cache/internal retries) yield no stack; the field is
//   absent and consumers must treat it as "unavailable", not "empty".
// - Worker-initiated requests open their channels without worker JS on the main
//   thread stack, so their initiator is unavailable here (DevTools uses the
//   alternate-stack mechanism; not ported).

const MAX_STACK_DEPTH = 32;
const MESSAGE_NAME = 'juggler:initiator-stack';

function isChromeFilename(filename) {
  return !filename || filename.startsWith('resource://') || filename.startsWith('chrome://');
}

export class InitiatorStackCollector {
  constructor(topBrowsingContext) {
    this._topBrowsingContextId = topBrowsingContext.id;
    this._observer = {
      observe: (subject, topic) => this._observe(subject, topic),
    };
    Services.obs.addObserver(this._observer, 'http-on-opening-request');
    Services.obs.addObserver(this._observer, 'document-on-opening-request');
  }

  dispose() {
    Services.obs.removeObserver(this._observer, 'http-on-opening-request');
    Services.obs.removeObserver(this._observer, 'document-on-opening-request');
  }

  _observe(subject, topic) {
    let channel;
    try {
      channel = subject.QueryInterface(Components.interfaces.nsIHttpChannel);
    } catch (e) {
      return;  // Not an HTTP channel (file:, data:, ...); ignore.
    }
    try {
      // Only attribute channels owned by this collector's tab. A content
      // process may host several top-level tabs, each with its own collector.
      const loadInfo = channel.loadInfo;
      const browsingContext = loadInfo && (loadInfo.frameBrowsingContext || loadInfo.browsingContext);
      if (!browsingContext || !browsingContext.top || browsingContext.top.id !== this._topBrowsingContextId)
        return;
    } catch (e) {
      return;
    }

    const stack = [];
    try {
      let frame = Components.stack;
      if (frame && frame.caller) {
        frame = frame.caller;  // Skip this observer callback itself.
        while (frame && stack.length < MAX_STACK_DEPTH) {
          if (!isChromeFilename(frame.filename)) {
            stack.push({
              functionName: frame.name || null,
              filename: frame.filename || null,
              lineNumber: frame.lineNumber,
              columnNumber: frame.columnNumber,
              asyncCause: frame.asyncCause || undefined,
            });
          }
          frame = frame.caller || frame.asyncCaller;
        }
      }
    } catch (e) {
      return;
    }

    if (!stack.length)
      return;  // No page JS on the stack: browser-initiated, nothing attributable.

    try {
      Services.cpmm.sendAsyncMessage(MESSAGE_NAME, {
        channelId: channel.channelId + '',
        stack,
      });
    } catch (e) {
      // Parent may be gone during shutdown; drop silently.
    }
  }
}

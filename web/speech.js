(() => {
  function consumeFinalResults(results, startIndex, handled) {
    const parts = [];
    let confidence = 1;
    for (let index = startIndex; index < results.length; index++) {
      const result = results[index];
      if (!result?.isFinal || handled.has(index)) continue;
      handled.add(index);
      const transcript = String(result[0]?.transcript || '').trim();
      if (transcript) parts.push(transcript);
      const score = Number(result[0]?.confidence);
      if (Number.isFinite(score)) confidence = Math.min(confidence, score);
    }
    return { transcript: parts.join(' ').trim(), confidence };
  }

  globalThis.RipwaveSpeech = { consumeFinalResults };
})();

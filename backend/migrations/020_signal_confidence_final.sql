-- Migration 020: Add confidence_final column to copy_trade_signals
--
-- The signal_generator computes C_final = 0.5*C_tx + 0.5*whale_score at signal
-- creation time, but only stores confidence_score (C_tx) in the DB. The history
-- endpoint currently recomputes C_final from confidence_score + score_at_generation,
-- which is fragile and loses precision. This migration persists C_final at insert
-- time so the history endpoint can return the exact value.
--
-- Backfill: for existing rows, recompute from confidence_score + score_at_generation
-- (the same formula used in signal_generator.py).

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'copy_trade_signals'
          AND column_name = 'confidence_final'
    ) THEN
        ALTER TABLE copy_trade_signals
            ADD COLUMN confidence_final REAL;

        -- Backfill existing rows with the same blend formula
        UPDATE copy_trade_signals
        SET confidence_final = ROUND(
            0.5 * COALESCE(confidence_score, 0)
            + 0.5 * COALESCE(score_at_generation, 0), 2
        )
        WHERE confidence_final IS NULL;
    END IF;
END$$;

-- Index for confidence-based filtering/sorting in signal history queries
CREATE INDEX IF NOT EXISTS idx_copy_trade_signals_confidence_final
    ON copy_trade_signals(confidence_final)
    WHERE confidence_final IS NOT NULL;

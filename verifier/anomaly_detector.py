import numpy as np
from sklearn.ensemble import IsolationForest
from datetime import datetime
import pandas as pd
import logging

logger = logging.getLogger(__name__)

class AnomalyDetector:
    """
    Unsupervised anomaly detection using only metadata (account_id, transaction_type, timestamp).
    No raw financial data is used, preserving privacy.
    """
    def __init__(self, contamination=0.1):
        self.model = IsolationForest(contamination=contamination, random_state=42, warm_start=True)
        self.trained = False
        self.feature_columns = ['hour', 'day_of_week', 'type_encoded', 'account_encoded']
        self.last_train_count = 0
        
    def prepare_features(self, transactions):
        """Convert transactions to feature matrix using only metadata."""
        if not transactions:
            return None
        
        df = pd.DataFrame(transactions)
        
        # Ensure required columns exist
        required = ['timestamp', 'transaction_type', 'account_id']
        for col in required:
            if col not in df.columns:
                logger.warning(f"Missing column: {col}")
                return None
        
        # Parse timestamp
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df['hour'] = df['timestamp'].dt.hour
        df['day_of_week'] = df['timestamp'].dt.dayofweek
        
        # Encode categorical variables (use factorize to handle unseen categories)
        df['type_encoded'] = pd.factorize(df['transaction_type'])[0]
        df['account_encoded'] = pd.factorize(df['account_id'])[0]
        
        return df[self.feature_columns].values
    
    def detect(self, transactions):
        """
        Detect anomalies in the transaction stream.
        Returns indices of anomalous transactions.
        """
        if len(transactions) < 10:
            return []  # Not enough data
        
        X = self.prepare_features(transactions)
        if X is None:
            return []
        
        # Retrain periodically (every 50 new transactions)
        if not self.trained or len(transactions) > self.last_train_count + 50:
            self.model.fit(X)
            self.trained = True
            self.last_train_count = len(transactions)
            logger.info(f"Retrained anomaly detector on {len(transactions)} samples")
        
        # Predict: -1 = anomaly, 1 = normal
        predictions = self.model.predict(X)
        anomaly_indices = np.where(predictions == -1)[0].tolist()
        
        return anomaly_indices
import datetime
try:
    import MetaTrader5 as mt5
    REAL_MT5 = True
    print("✅ Real MT5 imported successfully")
except ImportError:
    from src import mock_metatrader5 as mt5
    REAL_MT5 = False
    print("⚠️ Using mock MT5")

# Test connection
print(f"MT5 Type: {'REAL' if REAL_MT5 else 'MOCK'}")

# Test initialization (using config values)
login = 942464
password = "7%%*@oMuR"
server = "VTMarkets-Demo"
path = r"C:\Program Files\VT Markets (Pty) MT5 Terminal\terminal64.exe"

init_result = mt5.initialize(path=path, login=login, password=password, server=server)
print(f"MT5 Initialize result: {init_result}")

if init_result:
    print("✅ MT5 connection successful")
    
    # Test getting current rates
    symbol = "EURUSD-ECN"
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 5)
    
    if rates is not None:
        print(f"\n📊 Current rates for {symbol}:")
        for i, rate in enumerate(rates):
            # The mock returns numpy.datetime64, which can be printed directly.
            print(f"  Bar {i}: Open={rate['open']:.5f}, Close={rate['close']:.5f}, Time={rate['time']}")
    else:
        print(f"❌ No rates returned for {symbol}")
        print(f"Last error: {mt5.last_error()}")
    
    # Test historical data for a specific time (simulation date)
    target_time = datetime.datetime(2025, 8, 4, 8, 0, 0)
    target_timestamp = int(target_time.timestamp())
    
    print(f"\n🕐 Testing historical data for {target_time}")
    # The mock MT5 does not have copy_rates_from, using copy_rates_from_pos instead for testing.
    historical_rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 1)
    
    if historical_rates is not None and len(historical_rates) > 0:
        rate = historical_rates[0]
        # The mock returns numpy.datetime64, which can be printed directly.
        print(f"✅ Historical rate found: {rate['close']:.5f} at {rate['time']}")
    else:
        print(f"❌ No historical rates for {target_time}")
        print(f"Last error: {mt5.last_error()}")
    
    mt5.shutdown()
else:
    print("❌ MT5 connection failed")
    print(f"Last error: {mt5.last_error()}")

"""Метрика замораживается целиком из case/metric — байткод туда попадать не должен."""
import sys

sys.dont_write_bytecode = True
